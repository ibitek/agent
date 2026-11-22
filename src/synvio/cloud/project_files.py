"""Versioned project files. Manual edits create reversible revisions, never AI calls."""
import hashlib
import json
import time
import uuid
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from .common import safe_path, validate_files
from .store import Store

router=APIRouter(prefix='/api/tasks')

def digest(files): return hashlib.sha256(json.dumps(files,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def source(store,value):
    from .app import task_row
    task=task_row(store,value)
    files=store.get_json('files',value)
    if files is None: files=store.get_json('preview-files',value)
    if files is None: files=store.get_json('baseline',value,{})
    return task,files

@router.get('/{value}/project-files')
def listing(value:str):
    store=Store();task,files=source(store,value)
    info=store.get_json('github-project',task.get('project_id') or task.get('thread_id',value)) or {}
    items=[{'path':name,'size_bytes':len(text.encode()),'retained':False} for name,text in sorted(files.items())]
    items += [{'path':item['path'],'retained':True,'reason':item['reason']} for item in info.get('retained',[]) if item['path'] not in files]
    return {'items':items, 'version':digest(files),'read_only':task['status'] in {'running','queued'}}

@router.get('/{value}/project-files/content')
def content(value:str,file:str=Query(min_length=1,max_length=200)):
    try: name=safe_path(file)
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    _,files=source(Store(),value)
    if name not in files: raise HTTPException(404,'File not found in this project revision.')
    return {'path':name,'content':files[name],'version':digest(files)}

@router.get('/{value}/revisions')
def revisions(value:str):
    store=Store();task,_=source(store,value);thread=task.get('thread_id',value)
    items=[item for item in store.history(200) if item.get('thread_id',item['id'])==thread and store.get_json('files',item['id']) is not None]
    return {'items':[{key:item.get(key) for key in ('id','task','created_at','status','operation')} for item in items]}

class Edit(BaseModel):
    request_id:str=Field(pattern=r'^[a-f0-9]{32}$')
    expected_version:str=Field(pattern=r'^[a-f0-9]{64}$')
    operation:Literal['write','create','rename','delete','restore']
    file:str=Field(default='',max_length=200)
    target:str=Field(default='',max_length=200)
    content:str=Field(default='',max_length=262144)
    restore_id:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    conversation_id:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')

@router.post('/{value}/project-files')
def edit(value:str,body:Edit):
    store=Store();cached=store.get_json('file-edit',body.request_id)
    fingerprint=hashlib.sha256((value+body.model_dump_json()).encode()).hexdigest()
    if cached:
        if cached['fingerprint']!=fingerprint: raise HTTPException(409,'This edit identifier belongs to another change.')
        return cached['result']
    task,files=source(store,value)
    if task['status'] in {'running','queued'}: raise HTTPException(409,'Wait for the agent to finish before editing these files.')
    if digest(files)!=body.expected_version: raise HTTPException(409,'The files changed. Reload before saving.')
    info=store.get_json('github-project',task.get('project_id') or task.get('thread_id',value)) or {}
    retained={item['path'] for item in info.get('retained',[])}
    if body.file in retained or body.target in retained: raise HTTPException(409,'This path is retained in GitHub and is not editable in this workspace.')
    candidate=dict(files)
    try:
        if body.operation=='restore':
            other=store.get(body.restore_id or '')
            if not other or other.get('thread_id',other['id'])!=task.get('thread_id',value): raise ValueError('Choose a revision of this project.')
            candidate=store.get_json('files',body.restore_id)
            if candidate is None: raise ValueError('This revision has expired.')
        else:
            name=safe_path(body.file)
            if body.operation in {'write','create'}:
                if body.operation=='create' and name in candidate:
                    raise HTTPException(409,'A file already exists at this path. Choose another name.')
                candidate[name]=body.content
            else:
                if name not in candidate: raise ValueError('File not found.')
                if body.operation=='rename':
                    target=safe_path(body.target)
                    if target in candidate: raise ValueError('The destination file already exists.')
                    candidate[target]=candidate[name]
                del candidate[name]
        candidate=validate_files(candidate)
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    thread=task.get('thread_id',value);head=store.get_json('thread-head',thread,value)
    if head!=value: raise HTTPException(409,'A newer revision exists. Open it before editing.')
    new_id=uuid.uuid4().hex;now=time.time()
    item={**task,'id':new_id,'parent_id':value,'thread_id':thread,'status':'queued','task':'Manual '+body.operation+': '+body.file,'operation':'manual_edit','created_at':now,'updated_at':now,'deadline':now+60,'report':None,'error':None,'cost_record':None}
    # Global lock also serializes imports and agent writes. Do not mutate the old revision.
    if not store.create(item): raise HTTPException(409,'Project files are busy. Retry after the current operation finishes.')
    published=False
    try:
        if store.get_json('thread-head',thread,value)!=value: raise HTTPException(409,'A newer revision was saved. Reload before editing.')
        store.put_json('baseline',new_id,files)
        report={'success':True,'reason':'manual_edit','verification_level':'files_only','browser_verified':False,'files_created':[p for p in candidate if p not in files],'files_modified':[p for p in candidate if p in files and candidate[p]!=files[p]],'files_deleted':[p for p in files if p not in candidate],'attempt_details':[{'final_answer':'Files saved as a new revision. The previous revision can be restored.'}],'tests':[]}
        store.finish(item,candidate,'completed',report)
        published=True
        result={'id':new_id,'status':'completed','version':digest(candidate),'model_calls':0}
        store.put_json('file-edit',body.request_id,{'fingerprint':fingerprint,'result':result})
        store.event(new_id,{'event':'task_finished','status':'completed','success':True})
        if body.conversation_id:
            conversation=store.get_json('conversation',body.conversation_id)
            if conversation and conversation.get('last_task_id')==value:
                conversation.update(last_task_id=new_id,updated_at=now)
                conversation['messages'].append({'id':body.request_id,'role':'assistant','kind':'manual_edit','content':'Project files saved as a new revision.','task_id':new_id,'created_at':now,'local_response':True})
                store.put_json('conversation',body.conversation_id,conversation)
        result={'id':new_id,'status':'completed','version':digest(candidate),'model_calls':0}
        store.put_json('file-edit',body.request_id,{'fingerprint':fingerprint,'result':result})
        return result
    except Exception:
        if not published: store.fields(new_id,status='failed',error='The file revision could not be published. The original revision is intact.')
        raise
    finally: store.release(new_id)
