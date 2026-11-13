"""Browser security and SDK contract tests. No real VM or model is contacted."""
import json
import time
from types import SimpleNamespace
import pytest
from test_cloud_api import client, login
from synvio.cloud.browser_bridge import public_url
from synvio.cloud import browser_sessions as browser
from synvio.cloud.store import Store

ID='a'*32
REQ='b'*32

@pytest.mark.parametrize('url',['http://127.0.0.1','http://10.0.0.1','http://[::1]','http://localhost','http://x.local','http://metadata.google.internal','file:///etc/passwd','https://user:pass@example.com','https://example.com:8765','http://169.254.169.254','http://192.168.1.1','http://[fc00::1]'])
def test_private_destinations_rejected(url):
    with pytest.raises(ValueError): public_url(url)

@pytest.mark.parametrize('url',['https://example.com/a','http://example.com','https://1.1.1.1'])
def test_public_destinations_allowed(url):
    assert public_url(url)==url

@pytest.fixture
def remote(client,monkeypatch):
    from vercel import sandbox
    calls=[]
    class Files:
        async def write_text(self,*args): calls.append(('write',args))
    class Box:
        name='test-browser-vm'
        fs=Files()
        current_session=None
        async def run_command(self,command,args,**kwargs):
            calls.append(('run',command,args,kwargs))
            if 'stdout' in kwargs:
                kwargs['stdout'].write(json.dumps({'screenshot':'data:image/jpeg;base64,AAAA','captured_at':time.time(),'url':'https://example.com','links':[{'url':'https://example.com/about'},{'url':'https://evil.test'}]}))
            return SimpleNamespace(returncode=0)
        async def create_command(self,*args,**kwargs): calls.append(('launch',args,kwargs))
        async def destroy(self,**kwargs): calls.append(('destroy',kwargs))
        async def stop(self): calls.append(('stop',))
        async def update(self,**kwargs): calls.append(('update',kwargs))
        async def snapshot(self,**kwargs): return SimpleNamespace(id='blank-snapshot')
    box=Box();box.current_session=box
    async def create(**kwargs):calls.append(('create',kwargs));return box
    async def get(**kwargs):calls.append(('get',kwargs));return box
    monkeypatch.setattr(sandbox,'create_sandbox',create)
    monkeypatch.setattr(sandbox,'get_sandbox',get)
    login(client)
    return box,calls


def seed():
    store=Store()
    store.put_json('browser-session',ID,{'id':ID,'name':'test-browser-vm','status':'running','started_at':time.time(),'deadline':time.time()+300,'control_token':'private-control-secret'},expiry=900)
    store.put_json('browser-current','owner',ID,expiry=900)
    return store


def test_routes_private(client):
    assert client.get('/api/browser/status').status_code==401
    assert client.post('/api/browser/prepare').status_code==401
    assert client.post('/api/browser/sessions',json={'request_id':REQ}).status_code==401


def test_status_never_exposes_control_secret(client,remote):
    seed()
    response=client.get('/api/browser/status')
    assert response.status_code==200
    assert 'private-control-secret' not in response.text
    assert 'test-browser-vm' not in response.text


def test_prepare_and_start_without_host_secrets(client,remote):
    _,calls=remote
    assert client.post('/api/browser/prepare').status_code==200
    assert ('stop',) in calls
    response=client.post('/api/browser/sessions',json={'request_id':REQ})
    assert response.status_code==200,response.text
    assert response.json()['status']=='running'
    created=[call[1] for call in calls if call[0]=='create']
    assert len(created)==2
    assert all(call['env']=={} and call['ports']==[] for call in created)
    assert created[1]['execution_time_limit']==300
    assert '127.0.0.0/8' in created[1]['network_policy'].subnets.deny
    assert client.post('/api/browser/sessions',json={'request_id':REQ}).json()==response.json()
    assert len([call for call in calls if call[0]=='create'])==2
    value=response.json()['id']
    assert client.post(f'/api/browser/sessions/{value}/stop').json()['status']=='stopped'
    assert ('destroy',{}) in calls


def test_private_navigation_rejected_before_vm_lookup(client,remote):
    _,calls=remote;seed()
    response=client.post(f'/api/browser/sessions/{ID}/actions',json={'request_id':REQ,'operation':'navigate','url':'http://127.0.0.1'})
    assert response.status_code==422
    assert not calls


def test_no_automatic_repeat_or_resume(client,remote):
    box,calls=remote;seed()
    body={'request_id':REQ,'operation':'click','x':100,'y':100}
    assert client.post(f'/api/browser/sessions/{ID}/actions',json=body).status_code==200
    assert client.post(f'/api/browser/sessions/{ID}/actions',json=body).status_code==409
    assert len([call for call in calls if call[0]=='run'])==1
    box.current_session=None
    assert client.post(f'/api/browser/sessions/{ID}/actions',json={'request_id':'c'*32,'operation':'capture'}).status_code==409
    assert len([call for call in calls if call[0]=='run'])==1


def test_stop_blocks_all_future_actions(client,remote):
    seed()
    assert client.post(f'/api/browser/sessions/{ID}/stop').status_code==200
    assert client.post(f'/api/browser/sessions/{ID}/actions',json={'request_id':REQ,'operation':'capture'}).status_code==409
    assert Store().get_json('browser-session',ID)['control_token'] is None


def test_agent_permissions(client,remote):
    import asyncio
    from fastapi import HTTPException
    seed()
    for operation in ['click','type','key','new_tab','close_tab','scroll']:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(browser.perform(ID,browser.Action(request_id=REQ,operation=operation),actor='agent'))
        assert exc.value.status_code==403
    with pytest.raises(HTTPException) as exc:
        asyncio.run(browser.perform(ID,browser.Action(request_id=REQ,operation='navigate',url='https://example.com'),actor='agent'))
    assert exc.value.status_code==403
    asyncio.run(browser.perform(ID,browser.Action(request_id=REQ,operation='inspect'),actor='agent'))
    assert Store().get_json('browser-links',ID)==['https://example.com/about']


def test_expired_session_cannot_allocate_or_act(client,remote):
    _,calls=remote;store=seed();item=store.get_json('browser-session',ID);item['deadline']=0;store.put_json('browser-session',ID,item)
    assert client.post(f'/api/browser/sessions/{ID}/actions',json={'request_id':REQ,'operation':'capture'}).status_code==409
    assert not calls
    assert client.post(f'/api/browser/sessions/{ID}/stop').json()['status']=='stopped'
