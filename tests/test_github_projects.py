"""GitHub import and publication contracts on fake API responses only."""
import base64
import pytest
from test_cloud_api import client, login
from synvio.cloud import github
from synvio.cloud.store import Store

@pytest.fixture
def github_api(client,monkeypatch):
    calls=[]
    async def request(method,path,token=None,data=None):
        calls.append((method,path,token,data))
        if path=='/repos/owner/repo': return {'id':42,'default_branch':'main'}
        if '/commits/main' in path: return {'sha':'c'*40,'commit':{'tree':{'sha':'d'*40}}}
        if '?recursive=1' in path: return {'truncated':False,'tree':[
            {'type':'blob','mode':'100644','path':'index.html','size':12,'sha':'html'},
            {'type':'blob','mode':'100644','path':'src/app.js','size':10,'sha':'js'},
            {'type':'blob','mode':'100644','path':'logo.png','size':4,'sha':'binary'},
            {'type':'blob','mode':'100644','path':'.env','size':10,'sha':'secret'},
            {'type':'blob','mode':'120000','path':'link','size':10,'sha':'link'},
        ]}
        if '/git/blobs/' in path:
            raw=b'\0\x80' if path.endswith('binary') else b'<h1>Hi</h1>' if path.endswith('html') else b'let x=1'
            return {'content':base64.b64encode(raw).decode()}
        if method=='POST' and path.endswith('/git/trees'): return {'sha':'new-tree'}
        if method=='POST' and path.endswith('/git/commits'): return {'sha':'new-commit'}
        if '/git/matching-refs/' in path: return []
        if path.endswith('/git/refs'): return {'ref':data['ref']}
        if '/pulls?' in path: return []
        if method=='POST' and path.endswith('/pulls'): return {'html_url':'https://github.com/owner/repo/pull/1'}
        pytest.fail('Unexpected GitHub request: '+path)
    monkeypatch.setattr(github,'api',request)
    for key in ('SYNVIO_GITHUB_APP_ID','SYNVIO_GITHUB_PRIVATE_KEY','SYNVIO_GITHUB_INSTALLATION_ID'):monkeypatch.delenv(key,raising=False)
    login(client)
    return calls


def imported(client):
    response=client.post('/api/github/import',json={'request_id':'e'*32,'repository':'https://github.com/owner/repo.git'})
    assert response.status_code==200,response.text
    return response.json()


def test_import_isolated_and_unsupported_paths_explicitly_retained(client,github_api):
    Store().put_json('project','default',{'old.txt':'KEEP'})
    result=imported(client);value=result['task_id'];store=Store()
    assert result['model_calls']==0
    assert result['editable_files']==2
    assert {item['path'] for item in result['retained_paths']}=={'.env','logo.png','link'}
    assert not any(call[1].endswith('/secret') for call in github_api)
    assert store.project()=={'old.txt':'KEEP'}
    assert store.get(value)['project_id']==value
    assert set(store.get_json('files',value))=={'index.html','src/app.js'}
    before=len(github_api)
    assert imported(client)==result
    assert len(github_api)==before


def test_import_rejects_arbitrary_host_before_network(client,github_api):
    response=client.post('/api/github/import',json={'request_id':'f'*32,'repository':'http://127.0.0.1/secrets'})
    assert response.status_code==422
    assert github_api==[]


def test_large_tree_not_silently_truncated(client,github_api,monkeypatch):
    original=github.api
    async def api(method,path,*args,**kwargs):
        if '?recursive=1' in path:return {'truncated':True,'tree':[]}
        return await original(method,path,*args,**kwargs)
    monkeypatch.setattr(github,'api',api)
    assert client.post('/api/github/import',json={'request_id':'a'*32,'repository':'owner/repo'}).status_code==413
    assert Store().history(10)==[]


def test_push_requires_connection_preserves_base_tree_and_never_merges(client,github_api,monkeypatch):
    value=imported(client)['task_id'];store=Store();files=store.get_json('files',value);files['index.html']='New page';store.put_json('files',value,files)
    body={'request_id':'a'*32,'title':'Update home page'}
    assert client.post(f'/api/github/projects/{value}/pull-request',json=body).status_code==409
    async def token(repo_id=None,write=False):
        assert repo_id==42 and write
        return 'test-scoped-token'
    monkeypatch.setattr(github,'installation_token',token)
    response=client.post(f'/api/github/projects/{value}/pull-request',json=body)
    assert response.status_code==200,response.text
    writes=[call for call in github_api if call[0]=='POST']
    tree=next(call[3] for call in writes if call[1].endswith('/git/trees'))
    assert tree['base_tree']=='d'*40
    assert [item['path'] for item in tree['tree']]==['index.html']
    pull=next(call[3] for call in writes if call[1].endswith('/pulls'))
    assert pull['draft'] is True and pull['base']=='main'
    assert not any('/merge' in call[1] or call[0]=='PATCH' for call in github_api)
    before=len(github_api)
    assert client.post(f'/api/github/projects/{value}/pull-request',json=body).json()==response.json()
    assert len(github_api)==before


def test_cannot_overwrite_unloaded_binary_path_on_publish(client,github_api,monkeypatch):
    value=imported(client)['task_id'];store=Store();files=store.get_json('files',value);files['logo.png']='bad';store.put_json('files',value,files)
    async def token(*args,**kwargs):return 'test-token'
    monkeypatch.setattr(github,'installation_token',token)
    result=client.post(f'/api/github/projects/{value}/pull-request',json={'request_id':'b'*32,'title':'Oops'})
    assert result.status_code==409
    assert not any(call[0]=='POST' for call in github_api)

def test_github_routes_require_owner_session(client):
    assert client.get('/api/github/status').status_code==401
    assert client.get('/api/github/repositories').status_code==401
    assert client.post('/api/github/import',json={'request_id':'a'*32,'repository':'owner/repo'}).status_code==401
    assert client.post('/api/github/projects/'+('a'*32)+'/pull-request',json={'request_id':'b'*32,'title':'Change'}).status_code==401
