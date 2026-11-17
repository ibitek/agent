"""All inference uses a fake upstream; no paid requests or VMs."""
import asyncio
import hashlib
import json
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from synvio.cloud import codex_proxy as proxy
from synvio.cloud.codex_cli import config, Events

ID='a'*32
TOKEN='scoped-test-token'


class Store:
    def __init__(self):
        self.task={'id':ID,'model':'gpt-6.1-sol','model_id':'codex:gpt-6.1-sol',
            'status':'running','deadline':time.time()+100,'model_budget_usd':.5,
            'codex_proxy_digest':hashlib.sha256(TOKEN.encode()).hexdigest()}
        self.lock=False; self.events=[]
    def get(self,value): return dict(self.task) if value==ID else None
    def key(self,*parts): return ':'.join(parts)
    def call(self,operation,*args):
        if operation=='SET':
            if self.lock: return False
            self.lock=True; return True
        self.lock=False
    def write_cost(self,value,cost): self.task['cost_record']=cost
    def event(self,value,event): self.events.append(event)


@pytest.mark.parametrize('change',[{'status':'paused'},{'deadline':0},{'model_id':'openai:low'}])
def test_proxy_credential_cannot_outlive_its_task(change):
    store=Store();store.task.update(change)
    with pytest.raises(HTTPException): proxy.authorize(store,ID,TOKEN)
    with pytest.raises(HTTPException): proxy.authorize(store,ID,'wrong')


def test_browser_codex_uses_the_same_scoped_auth():
    store=Store();store.task['model_id']='codex-browser:gpt-6.1-sol'
    assert proxy.authorize(store,ID,TOKEN)['id']==ID


def test_unlimited_proxy_reports_no_artificial_budget():
    task={**Store().task,'model_budget_usd':None}
    result=proxy.record(task,[],Decimal('123.45'),Decimal(0))
    assert result['budget_usd'] is None and result['service_budget_usd'] is None
    assert result['estimated_cost_usd']=='123.450000000'


def test_fixed_model_and_bounded_output_even_if_payload_requests_more():
    task=Store().task
    data,cost=proxy.estimate(task,{'model':task['model'],'input':'hello','max_output_tokens':90000,'service_tier':'priority'})
    assert data['service_tier']=='default' and data['max_output_tokens']==4096
    assert cost>Decimal('.04')
    for payload in [{'model':'other'},{'model':task['model'],'previous_response_id':'previous'},
                    {'model':task['model'],'tools':[{'type':'computer_use_preview'}]}]:
        with pytest.raises(HTTPException): proxy.estimate(task,payload)


@pytest.mark.parametrize('provider_status',[200,400,500])
def test_reservation_before_upstream_and_unknown_usage_blocks_retry(monkeypatch,provider_status):
    store=Store();monkeypatch.setattr(proxy,'Store',lambda:store)
    sent=[]
    class Upstream:
        status_code=provider_status
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def aread(self): return b'{"error":"Invalid option"}'
        def raise_for_status(self):
            if self.status_code>=500: raise RuntimeError('upstream failed')
        async def aiter_lines(self):
            yield 'data: '+json.dumps({'type':'response.completed','response':{'id':'resp_test',
                'usage':{'input_tokens':100,'output_tokens':20},'output':[]}})
            yield ''
    class Client:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        def stream(self,*args,**kwargs):
            assert Decimal(store.task['cost_record']['unresolved_reservation_usd'])>0
            sent.append(kwargs);return Upstream()
    monkeypatch.setenv('OPENAI_API_KEY','backend-only')
    monkeypatch.setattr(proxy.httpx,'AsyncClient',Client)
    class Request:
        headers={'authorization':'Bearer '+TOKEN}
        async def body(self): return json.dumps({'model':store.task['model'],'input':'hello'}).encode()
    async def execute():
        response=await proxy.response(ID,Request())
        return b''.join([part async for part in response.body_iterator])
    body=asyncio.run(execute())
    assert len(sent)==1 and not store.lock
    state=store.task['cost_record']['calls'][0]['state']
    assert state=={200:'recorded',400:'rejected',500:'unresolved'}[provider_status]
    if provider_status==500:
        with pytest.raises(HTTPException,match='429'): asyncio.run(execute())
        assert len(sent)==1
    assert b'backend-only' not in body


def test_cli_config_and_real_event_shapes_do_not_invent_progress():
    text=config('gpt-6.1-sol','https://example.com/api/codex-proxy/'+ID)
    assert 'env_key = "SYNVIO_PROXY_TOKEN"' in text and 'request_max_retries = 0' in text
    assert 'OPENAI_API_KEY' not in text and 'web_search = "disabled"' in text
    store=Store();events=Events(store,store.task)
    lines=[{'type':'thread.started','thread_id':'thread_test'},
        {'type':'item.completed','item':{'type':'command_execution','command':'npm run build','exit_code':1,'aggregated_output':'actual error'}},
        {'type':'item.completed','item':{'type':'file_change'}},
        {'type':'item.completed','item':{'type':'agent_message','text':'Done'}}]
    for item in lines: events.write(json.dumps(item)+'\n')
    assert events.thread=='thread_test' and events.dirty and events.answer=='Done'
    assert store.events[0]['output']=='actual error' and store.events[0]['exit_code']==1


def test_codex_live_events_use_real_started_commands_and_safe_changed_paths(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-private-key')
    store = Store()
    events = Events(store, store.task)
    payloads = [
        {'type':'item.started','item':{'id':'cmd1','type':'command_execution','command':'echo test-private-key'}},
        {'type':'item.completed','item':{'id':'cmd1','type':'command_execution','command':'echo test-private-key',
            'exit_code':0,'aggregated_output':'test-private-key'}},
        {'type':'item.completed','item':{'type':'file_change','status':'completed','changes':[
            {'path':'/vercel/sandbox/project/src/App.jsx','kind':'update'},
            {'path':'old.js','kind':'delete'}, {'path':'/outside/private.txt','kind':'update'},
            {'path':'.env','kind':'update'}, {'path':'../escape.txt','kind':'update'}]}},
        {'type':'item.completed','item':{'type':'reasoning','text':'private reasoning'}},
    ]
    stream=''.join(json.dumps(item)+'\n' for item in payloads)
    events.write(stream[:19]); events.write(stream[19:])
    assert [item['event'] for item in store.events] == ['command_started','command_result','tool_call','tool_call']
    assert store.events[0]['command_id'] == store.events[1]['command_id'] == 'cmd1'
    assert [item['path'] for item in store.events if item['event']=='tool_call'] == ['src/App.jsx','old.js']
    assert store.events[-1]['tool'] == 'dev_delete_file'
    assert 'test-private-key' not in json.dumps(store.events)
    assert 'private reasoning' not in json.dumps(store.events)


def test_preflight_reloads_cost_under_the_inference_lock(monkeypatch):
    store=Store();monkeypatch.setattr(proxy,'Store',lambda:store)
    class Request:
        headers={'authorization':'Bearer '+TOKEN}
        async def body(self):
            # Simulate a prior inference completing during a slow body upload.
            store.task['cost_record']={'estimated_cost_usd':'.49','calls':[]}
            return json.dumps({'model':store.task['model'],'input':'hello'}).encode()
    with pytest.raises(HTTPException) as caught:
        asyncio.run(proxy.response(ID,Request()))
    assert caught.value.status_code==429 and not store.lock
