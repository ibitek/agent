"""Owner GitHub App integration. Credentials stay on the API host, never in projects."""
import asyncio
import base64
from difflib import unified_diff
import hashlib
from itertools import islice
import json
import os
import re
import time
import uuid
from urllib.parse import quote
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from .common import MAX_PROJECT_BYTES, MAX_PROJECT_FILES, MAX_FILE_BYTES, safe_path, validate_files
from .store import Store

router=APIRouter(prefix='/api/github')
API='https://api.github.com'

def configured(): return all(os.environ.get(key) for key in ('SYNVIO_GITHUB_APP_ID','SYNVIO_GITHUB_PRIVATE_KEY','SYNVIO_GITHUB_INSTALLATION_ID'))

def app_jwt():
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    def enc(value): return base64.urlsafe_b64encode(value).rstrip(b'=')
    now=int(time.time())
    payload=enc(b'{"alg":"RS256","typ":"JWT"}')+b'.'+enc(json.dumps({'iat':now-60,'exp':now+540,'iss':os.environ['SYNVIO_GITHUB_APP_ID']}).encode())
    key=serialization.load_pem_private_key(os.environ['SYNVIO_GITHUB_PRIVATE_KEY'].replace('\\n','\n').encode(),password=None)
    return (payload+b'.'+enc(key.sign(payload,padding.PKCS1v15(),hashes.SHA256()))).decode()

async def api(method,path,token=None,data=None):
    if not path.startswith('/') or path.startswith('//'): raise ValueError('Invalid GitHub API path.')
    headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'SYNVIO-Core'}
    if token: headers['Authorization']='Bearer '+token
    async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:
        async with client.stream(method,API+path,headers=headers,json=data) as response:
            if response.status_code>=300:
                raise HTTPException(502,'GitHub request failed (HTTP '+str(response.status_code)+'). Check repository access, App permissions and rate limits. No request was automatically repeated.')
            raw=bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw)>8*1024*1024: raise HTTPException(413,'GitHub response exceeds the workspace import limit.')
            return json.loads(raw)

async def installation_token(repo_id=None,write=False):
    if not configured(): return None
    installation=os.environ['SYNVIO_GITHUB_INSTALLATION_ID']
    if not installation.isdigit(): raise HTTPException(503,'GitHub App installation is not configured correctly.')
    data={'permissions':{'contents':'write' if write else 'read'}}
    if write: data['permissions']['pull_requests']='write'
    if repo_id is not None: data['repository_ids']=[repo_id]
    result=await api('POST',f'/app/installations/{installation}/access_tokens',app_jwt(),data)
    return result['token']

@router.get('/status')
def status():
    slug=os.environ.get('SYNVIO_GITHUB_APP_SLUG','')
    return {'connected':configured(),'public_import':True,'install_url':f'https://github.com/apps/{slug}/installations/new' if re.fullmatch(r'[a-zA-Z0-9-]+',slug) else None,'max_editable_files':MAX_PROJECT_FILES,'max_editable_bytes':MAX_PROJECT_BYTES,'private_access_requires':'GitHub App installation'}

@router.get('/repositories')
async def repositories():
    token=await installation_token()
    if not token: raise HTTPException(409,'Connect the project GitHub App to list private repositories.')
    result=await api('GET','/installation/repositories?per_page=100',token)
    return {'items':[{'full_name':item['full_name'],'private':item['private'],'default_branch':item['default_branch']} for item in result['repositories']], 'total':result.get('total_count')}

class Import(BaseModel):
    use_connection:bool=False
    request_id:str=Field(pattern=r'^[a-f0-9]{32}$')
    repository:str=Field(min_length=3,max_length=200)
    branch:str|None=Field(default=None,max_length=200)

@router.post('/import')
async def import_repository(body:Import):
    store=Store(); fingerprint=hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    cached=store.get_json('github-import',body.request_id)
    if cached:
        if cached['fingerprint']!=fingerprint: raise HTTPException(409,'This import identifier belongs to another repository.')
        return cached['result']
    repo=body.repository.removeprefix('https://github.com/').removesuffix('.git').strip('/')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+',repo) or any(part in {'.','..'} for part in repo.split('/')): raise HTTPException(422,'Use owner/repository or its github.com URL.')
    lock=store.key('github-import-lock')
    if not store.call('SET',lock,body.request_id,'NX','EX',240): raise HTTPException(409,'A repository import is already in progress.')
    try:
        token=await installation_token() if body.use_connection else None
        if body.use_connection and not token: raise HTTPException(409,"Connect a GitHub App for private repository access.")
        metadata=await api('GET','/repos/'+repo,token)
        # Tokens are re-scoped to this one repository before reading its source.
        if token: token=await installation_token(metadata['id'])
        branch=body.branch or metadata['default_branch']
        commit=await api('GET','/repos/'+repo+'/commits/'+quote(branch,safe=''),token)
        sha=commit['sha'];tree_sha=commit['commit']['tree']['sha']
        tree=await api('GET','/repos/'+repo+'/git/trees/'+tree_sha+'?recursive=1',token)
        if tree.get('truncated'): raise HTTPException(413,'The repository tree is too large to import safely. No partial project was created.')
        editable=[];retained=[]
        for item in tree['tree']:
            if item['type']=='tree': continue
            try: safe_path(item['path'])
            except ValueError:
                retained.append({'path':item['path'],'reason':'protected or hidden path'});continue
            if item['type']!='blob' or item['mode'] not in {'100644','100755'}:
                retained.append({'path':item['path'],'reason':'symlink or submodule'});continue
            if item.get('size',MAX_FILE_BYTES+1)>MAX_FILE_BYTES:
                retained.append({'path':item['path'],'reason':'file size limit'});continue
            editable.append(item)
        if len(editable)>MAX_PROJECT_FILES or sum(item.get('size',0) for item in editable)>MAX_PROJECT_BYTES:
            raise HTTPException(413,'This repository exceeds the current editable workspace limit (100 files / 2 MiB). No partial project was created.')
        sem=asyncio.Semaphore(6)
        async def read(item):
            async with sem:
                blob=await api('GET','/repos/'+repo+'/git/blobs/'+item['sha'],token)
                raw=base64.b64decode(blob['content'])
                if len(raw)>MAX_FILE_BYTES: raise HTTPException(413,'A repository file exceeded the import limit.')
                try:
                    text=raw.decode('utf-8')
                    if '\0' in text: raise UnicodeError()
                    return item['path'],text
                except UnicodeError:
                    retained.append({'path':item['path'],'reason':'binary asset'});return None
        pairs=await asyncio.gather(*(read(item) for item in editable))
        files=validate_files(dict(pair for pair in pairs if pair is not None))
        value=uuid.uuid4().hex;now=time.time()
        try:
            package=json.loads(files.get('package.json','{}'))
            vite='vite' in {**package.get('dependencies',{}),**package.get('devDependencies',{})}
        except (ValueError,TypeError,AttributeError): vite=False
        runtime='app' if vite else 'source' if 'package.json' in files else 'web' if 'index.html' in files else 'python' if any(p.endswith('.py') for p in files) else 'source'
        task={'id':value,'project_id':value,'thread_id':value,'parent_id':None,'runtime':runtime,'operation':'import','task':'Import '+repo,'workspace':'project','model_id':'openai:low','model':'gpt-4o-mini','model_budget_usd':.1,'status':'queued','created_at':now,'updated_at':now,'deadline':now+60,'report':None,'error':None}
        if not store.create(task): raise HTTPException(409,'Another project operation is active. Wait before importing.')
        try:
            store.put_json('files',value,files)
            store.put_json('baseline',value,files)
            store.put_json('github-baseline',value,files,expiry=0)
            store.put_json('project',value,files,expiry=0)
            store.put_json('github-project',value,{'repository':repo,'repository_id':metadata['id'],'branch':branch,'commit_sha':sha,'tree_sha':tree_sha,'import_task_id':value,'retained':retained,'source_file_count':len([i for i in tree['tree'] if i['type']!='tree']),'modes':{i['path']:i['mode'] for i in editable}},expiry=0)
            store.put_json('thread-head',value,value)
            report={'success':True,'reason':'repository_imported','verification_level':'files_only','browser_verified':False,'files_created':list(files),'tests':[],'attempt_details':[{'final_answer':f'Imported {len(files)} editable files from {repo}. {len(retained)} protected, binary or unsupported paths remain in GitHub and are preserved when publishing changes. No code or tests were run.'}]}
            store.finish(task,files,'completed',report)
            store.event(value,{'event':'task_finished','status':'completed','success':True})
            conversation={'id':value,'project_id':value,'title':repo,'last_task_id':value,'updated_at':now,'messages':[{'id':value+'-import','role':'assistant','kind':'import','content':'Repository imported. Review the source, or describe what you want to change.','task_id':value,'local_response':True,'created_at':now}]}
            store.put_json('conversation',value,conversation)
            store.call('ZADD',store.key('conversations'),now,value)
            result={'conversation_id':value,'task_id':value,'editable_files':len(files),'retained_paths':retained,'model_calls':0}
            store.put_json('github-import',body.request_id,{'fingerprint':fingerprint,'result':result})
            return result
        finally: store.release(value)
    finally:
        store.call('EVAL',"if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end",1,lock,body.request_id)

@router.get('/projects/{value}')
def project_status(value:str):
    if not re.fullmatch(r'[a-f0-9]{32}',value): raise HTTPException(404,'Project not found.')
    store=Store();task=store.get(value)
    info=store.get_json('github-project',task.get('project_id') or task.get('thread_id',value)) if task else None
    return {'source':info,'publish_enabled':configured()}

class Publish(BaseModel):
    request_id:str=Field(pattern=r'^[a-f0-9]{32}$')
    title:str=Field(min_length=1,max_length=150)

def publication_source(store, value):
    if not re.fullmatch(r'[a-f0-9]{32}', value):
        raise HTTPException(404, 'Project not found.')
    task = store.get(value)
    if not task or task['status'] in {'running','queued'}: raise HTTPException(409,'Choose a finished project revision.')
    info = store.get_json('github-project', task.get('project_id') or task.get('thread_id', value))
    if not info: raise HTTPException(404,'This project was not imported from GitHub.')
    return info


def publication_changes(store, value, info):
    files = store.get_json('files', value)
    baseline = store.get_json('github-baseline', info['import_task_id'])
    if baseline is None:
        baseline = store.get_json('baseline', info['import_task_id'])
    if files is None or baseline is None:
        raise HTTPException(409, 'The source snapshot expired. Import the repository again before publishing.')
    retained = {item['path'] for item in info.get('retained', [])}
    if retained.intersection(files):
        raise HTTPException(409, 'A new editable file overlaps a retained repository path. Rename it before publishing.')
    changes = {}
    for path in sorted(files.keys() | baseline.keys()):
        if path not in files:
            changes[path] = 'deleted'
        elif path not in baseline:
            changes[path] = 'added'
        elif baseline[path] != files[path]:
            changes[path] = 'modified'
    return files, baseline, changes


@router.get('/projects/{value}/review')
def review(value: str, file: str | None = None):
    store = Store()
    info = publication_source(store, value)
    files, baseline, changes = publication_changes(store, value, info)
    if file is None:
        return {'files': [{'path': path, 'action': action} for path, action in changes.items()]}
    if file not in changes:
        raise HTTPException(404, 'This file has no changes in the selected revision.')
    before = baseline.get(file, '').splitlines(keepends=True)
    after = files.get(file, '').splitlines(keepends=True)
    diff = unified_diff(before, after, '/dev/null' if file not in baseline else f'a/{file}',
                        '/dev/null' if file not in files else f'b/{file}')
    lines = list(islice(diff, 301))
    excerpt = ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n' for line in lines[:300])
    return {'diff': excerpt[:16000], 'truncated': len(lines) > 300 or len(excerpt) > 16000}


@router.post('/projects/{value}/pull-request')
async def publish(value:str,body:Publish):
    store=Store()
    info = publication_source(store, value)
    fingerprint=hashlib.sha256((value+body.model_dump_json()).encode()).hexdigest()
    saved=store.get_json('github-publish',body.request_id) or {'fingerprint':fingerprint}
    if saved['fingerprint']!=fingerprint: raise HTTPException(409,'This publish identifier belongs to another change.')
    if saved.get('url'): return {'url':saved['url']}
    lock=store.key('github-publish-lock',body.request_id)
    if not store.call('SET',lock,'1','NX','EX',240): raise HTTPException(409,'This pull request is being prepared.')
    def save(): store.put_json('github-publish',body.request_id,saved)
    try:
        files, _, changed_paths = publication_changes(store, value, info)
        if not changed_paths:
            raise HTTPException(409, 'There are no source changes to publish.')
        token=await installation_token(info['repository_id'],write=True)
        if not token: raise HTTPException(409,'Connect a GitHub App with Contents and Pull requests write permissions before publishing.')
        repo=info['repository'];prefix='/repos/'+repo
        if not saved.get('branch'):
            upstream = await api('GET', prefix + '/commits/' + quote(info['branch'], safe=''), token)
            if upstream['sha'] != info['commit_sha']:
                raise HTTPException(409, 'The base branch has moved on GitHub. Import its latest revision before publishing.')
        changes = []
        for path, action in changed_paths.items():
            entry = {'path': safe_path(path), 'mode': info.get('modes', {}).get(path, '100644'), 'type': 'blob'}
            if action == 'deleted':
                entry['sha'] = None
            else:
                entry['content'] = files[path]
            changes.append(entry)
        if not saved.get('tree'):
            saved['tree']=(await api('POST',prefix+'/git/trees',token,{'base_tree':info['tree_sha'],'tree':changes}))['sha'];save()
        if not saved.get('commit'):
            saved['commit']=(await api('POST',prefix+'/git/commits',token,{'message':body.title,'tree':saved['tree'],'parents':[info['commit_sha']]}))['sha'];save()
        branch='synvio/'+value[:12]+'-'+body.request_id[:12]
        if not saved.get('branch'):
            # Read before writing: a lost response must not create a second branch.
            refs=await api('GET',prefix+'/git/matching-refs/heads/'+quote(branch,safe='/'),token)
            if refs:
                if refs[0]['object']['sha']!=saved['commit']: raise HTTPException(409,'The target branch changed. No force push was attempted.')
            else: await api('POST',prefix+'/git/refs',token,{'ref':'refs/heads/'+branch,'sha':saved['commit']})
            saved['branch']=branch;save()
        existing=await api('GET',prefix+'/pulls?state=all&head='+quote(repo.split('/')[0]+':'+branch,safe=''),token)
        result=existing[0] if existing else await api('POST',prefix+'/pulls',token,{'title':body.title,'head':branch,'base':info['branch'],'draft':True,'body':'Changes prepared in Kaelia. Review the diff and checks before merging. No automatic merge was requested.'})
        saved['url']=result['html_url'];save()
        return {'url':saved['url']}
    finally: store.call('DEL',lock)
