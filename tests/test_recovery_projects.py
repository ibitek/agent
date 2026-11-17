"""Durable continuation, project isolation and reversible editing. No paid services."""
import json
import time
import pytest
from test_cloud_api import client, login, create, assistant_engine
from synvio.cloud import app as cloud
from synvio.cloud.assistant import planning_only, wants_changes
from synvio.cloud.store import Store

@pytest.mark.parametrize('text',['Zaplanuj budowe strony','Please plan a new website','wait before changing files','Only plan the implementation'])
def test_planning_never_authorizes_work(text):
    assert planning_only(text)
    assert not wants_changes(text)

@pytest.mark.parametrize('text',['Implement the plan','Build a site according to the plan','Create a planning application'])
def test_implementing_a_plan_is_allowed(text):
    assert not planning_only(text)
    assert wants_changes(text)

def seeded(client,status='paused'):
    login(client);value=create(client).json()['id'];store=Store()
    store.fields(value,status=status)
    store.put_json('files',value,{'index.html':'<h1>Saved</h1>','src/main.js':'console.log(1)'})
    store.release(value)
    return store,value


def test_rate_limit_cooldown_blocks_restart_before_allocating_another_task(client):
    from synvio.cloud.interruptions import pause_details
    store,value=seeded(client)
    details=pause_details(RuntimeError('exceeded retry limit, last status: 429 Too Many Requests'))
    store.fields(value,**details)
    response=client.post(f'/api/tasks/{value}/resume',json={'request_id':'4'*32})
    assert response.status_code==429
    assert len(store.history(10))==1
    assert store.get_json('files',value)['index.html']=='<h1>Saved</h1>'


def test_resume_preserves_source_and_checkpoint_even_if_previous_start_was_rejected(client):
    store,value=seeded(client)
    checkpoint={'messages':[{'role':'system','content':'system'},{'role':'user','content':'request'}],
                'pending_action':{'action':'finish','text':'Saved'}}
    store.fields(value,runtime="web",resume_checkpoint=checkpoint)
    result=client.post(f'/api/tasks/{value}/resume',json={'request_id':'4'*32})
    assert result.status_code==200
    child=result.json()['task']['id']
    assert store.get_json('files',child)==store.get_json('files',value)
    assert store.get(child)['resume_checkpoint']==checkpoint


def test_private_checkpoint_is_not_exposed_by_task_or_history_or_resume(client):
    store, value = seeded(client)
    private = {'native_tool_state': {'output': [{'encrypted_content': 'private-state',
               'arguments': 'private-file-body'}]}, 'pending_action': None}
    store.fields(value, runtime='web', resume_checkpoint=private)
    for response in [client.get(f'/api/tasks/{value}'), client.get('/api/tasks')]:
        assert response.status_code == 200
        assert 'private-state' not in response.text
        assert 'private-file-body' not in response.text
        assert 'resume_checkpoint' not in response.text
    response = client.post(f'/api/tasks/{value}/resume', json={'request_id': '9' * 32})
    assert response.status_code == 200
    child = response.json()['task']['id']
    assert store.get(child)['resume_checkpoint'] == private
    assert 'private-state' not in client.get(f'/api/tasks/{child}').text


def test_resume_preserves_checkpoint_and_is_idempotent(client):
    store,value=seeded(client)
    checkpoint={'version':1,'messages':[{'role':'user','content':'Original request'}],'pending_action':{'action':'finish','text':'Done'},'changed':['index.html']}
    store.fields(value,runtime='web')
    store.put_json('checkpoint',value,checkpoint)
    body={'request_id':'b'*32,'model_budget_usd':.05}
    response=client.post(f'/api/tasks/{value}/resume',json=body)
    assert response.status_code==200,response.text
    child=response.json()['task']['id']
    assert child!=value
    assert store.get(child)['resume_checkpoint']==checkpoint
    assert store.get(child)['parent_id']==value
    assert store.get(child)['model_budget_usd']==.05
    assert client.post(f'/api/tasks/{value}/resume',json=body).json()==response.json()
    assert len(store.history(10))==2
    assert store.get(value)['resumed_by']==child


def test_unknown_provider_cost_requires_explicit_acknowledgment(client):
    store,value=seeded(client)
    store.fields(value,cost_record={'unresolved_reservation_usd':'0.01'})
    body={'request_id':'c'*32}
    assert client.post(f'/api/tasks/{value}/resume',json=body).status_code==409
    assert len(store.history(10))==1
    assert client.post(f'/api/tasks/{value}/resume',json={**body,'acknowledge_unknown_cost':True}).status_code==200


def test_checkpoint_refuses_late_worker_writes(client):
    store,value=seeded(client,'running')
    store.checkpoint(value,{'pending_action':None},{'index.html':'first'})
    store.fields(value,status='paused')
    with pytest.raises(RuntimeError): store.checkpoint(value,{}, {'index.html':'late'})
    assert store.get_json('files',value)=={'index.html':'first'}


def test_file_tree_contains_unchanged_files_and_preserves_prior_revisions(client):
    store,value=seeded(client,'completed')
    listing=client.get(f'/api/tasks/{value}/project-files').json()
    assert [i['path'] for i in listing['items']]==['index.html','src/main.js']
    assert client.get(f'/api/tasks/{value}/project-files/content?file=src/main.js').json()['content']=='console.log(1)'
    body={'request_id':'d'*32,'operation':'rename','file':'src/main.js','target':'src/app.js','expected_version':listing['version']}
    response=client.post(f'/api/tasks/{value}/project-files',json=body)
    assert response.status_code==200,response.text
    child=response.json()['id']
    assert 'src/main.js' in store.get_json('files',value)
    assert 'src/app.js' in store.get_json('files',child)
    assert client.post(f'/api/tasks/{value}/project-files',json=body).json()==response.json()
    assert client.post(f'/api/tasks/{value}/project-files',json={**body,'request_id':'e'*32}).status_code==409
    version=client.get(f'/api/tasks/{child}/project-files').json()['version']
    restored=client.post(f'/api/tasks/{child}/project-files',json={'request_id':'f'*32,'expected_version':version,'operation':'restore','restore_id':value})
    assert restored.status_code==200,restored.text
    assert store.get_json('files',restored.json()['id'])==store.get_json('files',value)


def test_file_edits_cannot_escape_project_or_run_during_work(client):
    store,value=seeded(client,'running')
    version=client.get(f'/api/tasks/{value}/project-files').json()['version']
    body={'request_id':'a'*32,'operation':'write','file':'../escape','content':'bad','expected_version':version}
    assert client.post(f'/api/tasks/{value}/project-files',json=body).status_code==409
    store.fields(value,status='completed')
    assert client.post(f'/api/tasks/{value}/project-files',json=body).status_code==422


def test_new_conversation_does_not_read_previous_global_project(client,monkeypatch):
    login(client);Store().put_json('project','default',{'private-project.txt':'OTHER PROJECT'})
    calls=assistant_engine(monkeypatch,[{'action':'list'},{'action':'reply','text':'New project.'}])
    result=client.post('/api/assistant',json={'request_id':'1'*32,'message':'Explain the current project'})
    assert result.status_code==200
    assert 'private-project.txt' not in calls[-1][0][-1].content


def test_planning_router_error_cannot_start_an_execution(client,monkeypatch):
    login(client)
    assistant_engine(monkeypatch,[{'action':'execute','task':'Build a website'},{'action':'reply','text':'Here is the plan: home and about pages.'}])
    result=client.post('/api/assistant',json={'request_id':'2'*32,'message':'Plan a website with two pages'})
    assert result.status_code==200,result.text
    assert result.json()['task'] is None
    assert 'plan' in result.json()['message']['content']
    assert Store().history(10)==[]

def test_web_checkpoint_continues_without_repeating_completed_write(client):
    from synvio.cloud.web_builder import build
    store,value=seeded(client,'running')
    # Re-establish ownership as a real worker has it.
    store.call('SET',store.key('project-lock'),value,'EX',300)
    store.fields(value,runtime='web')
    class Engine:
        def __init__(self,actions):self.actions=iter(actions);self.calls=0
        def generate(self,*args,**kwargs):
            self.calls+=1
            result=next(self.actions)
            if isinstance(result,Exception):raise result
            return {'content':json.dumps(result)}
        def summary(self):return {'estimated_cost_usd':'0.001'}
    first=Engine([{'action':'write','path':'index.html','content':'<h1>Completed step</h1>'},RuntimeError('connection lost')])
    with pytest.raises(RuntimeError,match='connection lost'):
        build(store,store.get(value),{'index.html':'Before'},first)
    assert store.get_json('files',value)['index.html']=='<h1>Completed step</h1>'
    assert store.get_json('checkpoint',value)['pending_action'] is None
    store.fields(value,status='paused');store.release(value)
    response=client.post(f'/api/tasks/{value}/resume',json={'request_id':'3'*32})
    assert response.status_code==200,response.text
    child=response.json()['task']['id'];store.fields(child,status='running')
    second=Engine([{'action':'finish','text':'Completed saved work.'}])
    build(store,store.get(child),store.get_json('files',value),second)
    assert second.calls==1
    assert store.get(child)['status']=='completed'
    assert store.get_json('files',child)['index.html']=='<h1>Completed step</h1>'


def test_late_worker_cannot_publish_over_new_revision(client):
    store,value=seeded(client,'running')
    store.call('SET',store.key('project-lock'),'another-worker','EX',300)
    store.put_json('project','default',{'new.txt':'Newest'})
    with pytest.raises(RuntimeError,match='no longer owns'):
        store.finish(store.get(value),{'old.txt':'Late'},'completed',{'success':True})
    assert store.project()=={'new.txt':'Newest'}

def test_latest_project_survives_expired_run_and_conversation(client):
    store,value=seeded(client,'running');project='7'*32
    store.fields(value,project_id=project)
    store.call('SET',store.key('project-lock'),value,'EX',300)
    store.finish(store.get(value),{'index.html':'Persistent latest'},'completed',{'success':True})
    store.release(value)
    store.call('DEL',store.key('task',value),store.key('files',value),store.key('conversation',project))
    listing=client.get('/api/projects').json()['items']
    assert listing[0]['id']==project
    reopened=client.post(f'/api/projects/{project}/open')
    assert reopened.status_code==200,reopened.text
    chat=store.get_json('conversation',project)
    assert store.get_json('files',chat['last_task_id'])=={'index.html':'Persistent latest'}
    assert store.get(chat['last_task_id'])['operation']=='import'


def test_open_project_does_not_hide_active_continuation(client):
    store,value=seeded(client,'running');project='8'*32
    store.fields(value,project_id=project)
    store.put_json('project-meta',project,{'id':project,'head':'9'*32,'title':'Project'})
    store.put_json('conversation',project,{'id':project,'project_id':project,'last_task_id':value,'messages':[]})
    assert client.post(f'/api/projects/{project}/open').status_code==200
    assert store.get_json('conversation',project)['last_task_id']==value


def test_files_cannot_collide_with_folders():
    from synvio.cloud.common import validate_files
    with pytest.raises(ValueError,match='both a file and a folder'):
        validate_files({'src':'file','src/main.js':'nested'})

def test_claimed_worker_survives_http_response_without_duplicate_claim(client,monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import vercel.functions
    import vercel.cache.context
    store,value=seeded(client,'queued')
    store.call('SET',store.key('project-lock'),value,'EX',300)
    pending=[];calls=[]
    monkeypatch.setenv('VERCEL','1')
    monkeypatch.setattr(vercel.cache.context,'get_context',lambda:SimpleNamespace(wait_until=True))
    monkeypatch.setattr(vercel.functions,'wait_until',lambda work:pending.append(work))
    async def worker(store,value,oidc_token=None):
        calls.append(value)
        store.fields(value,status='completed')
        store.release(value)
    monkeypatch.setattr(cloud,'run_worker',worker)
    response=client.post(f'/api/tasks/{value}/run')
    assert response.status_code==200
    assert response.json()['status']=='running'
    assert calls==[] and len(pending)==1
    assert client.post(f'/api/tasks/{value}/run').json()['status']=='running'
    assert len(pending)==1
    asyncio.run(pending[0])
    assert calls==[value]
    assert store.get(value)['status']=='completed'

def test_lost_worker_ownership_blocks_new_paid_reservation(client):
    from synvio.cloud.metering import MeteredEngine
    from openjarvis.core.types import Message,Role
    store,value=seeded(client,'running')
    store.call('SET',store.key('project-lock'),'new-owner','EX',300)
    class Engine:
        def generate(self,*args,**kwargs):pytest.fail('A stale worker must not call a model')
    meter=MeteredEngine(Engine(),'gpt-4o-mini',.1,lambda event:store.write_cost(value,event))
    with pytest.raises(RuntimeError,match='No new model call'):
        meter.generate([Message(role=Role.USER,content='Continue')],model='gpt-4o-mini',max_tokens=10)
    assert store.get(value).get('cost_record') is None

def test_resume_identifier_cannot_be_reused_with_a_different_spending_cap(client):
    store,value=seeded(client)
    body={'request_id':'4'*32,'model_budget_usd':.1}
    assert client.post(f'/api/tasks/{value}/resume',json=body).status_code==200
    assert client.post(f'/api/tasks/{value}/resume',json={**body,'model_budget_usd':.02}).status_code==409
    assert client.post(f'/api/tasks/{value}/resume',json={**body,'request_id':'5'*32,'model_budget_usd':.02}).status_code==409
    assert len(store.history(10))==2


def test_direct_build_request_with_other_project_restriction_skips_router(client,monkeypatch):
    login(client)
    calls=assistant_engine(monkeypatch,[])
    result=client.post('/api/assistant',json={'request_id':'1'*32,'message':'Zbuduj strone w React + Vite. Nie zmieniaj innych projektow.'})
    assert result.status_code==200,result.text
    assert result.json()['message']['kind']=='execute'
    assert result.json()['task']['status']=='queued'
    assert calls==[]


def test_start_doing_it_after_plan_uses_context_without_second_confirmation(client,monkeypatch):
    login(client);conversation='2'*32
    Store().put_json('conversation',conversation,{'id':conversation,'project_id':conversation,'last_task_id':None,'messages':[{'role':'user','content':'Plan a React website with two pages'},{'role':'assistant','kind':'reply','content':'Home and Pricing with a demo form.'}]})
    calls=assistant_engine(monkeypatch,[])
    result=client.post('/api/assistant',json={'request_id':'3'*32,'conversation_id':conversation,'message':'okay start doing it'})
    assert result.status_code==200,result.text
    task=Store().get(result.json()['task']['id'])
    assert 'Home and Pricing' in task['task'] and task['runtime']=='app'
    assert result.json()['message']['confirmation'] is None and calls==[]


def test_rebuild_chat_command_does_not_invoke_model_or_file_worker(client,monkeypatch):
    store,value=seeded(client,'completed');conversation='4'*32
    store.put_json('conversation',conversation,{'id':conversation,'last_task_id':value,'messages':[]})
    calls=assistant_engine(monkeypatch,[]);builds=[]
    async def fake_build(value,body):builds.append(value);return {'status':'building'}
    monkeypatch.setattr(cloud,'build_application_preview',fake_build)
    result=client.post('/api/assistant',json={'request_id':'5'*32,'conversation_id':conversation,'message':'rebuild preview'})
    assert result.status_code==200,result.text
    assert result.json()['message']['kind']=='build'
    assert result.json()['task'] is None and calls==[] and builds==[value]


def test_old_empty_build_is_not_presented_as_available_preview(client):
    store,value=seeded(client,'completed');store.fields(value,runtime='app')
    store.put_json('files',value,{'package.json':json.dumps({'devDependencies':{'vite':'6.4.6'},'scripts':{'build':'vite build'}}),'index.html':'<div id="app"></div>'})
    store.put_json('built-files',value,{'dist/index.html':'<div id="app"></div>'})
    result=client.get(f'/api/tasks/{value}/preview').json()
    assert not result['available'] and result['source_invalid']
    assert 'module script' in result['reason']
