import json
from types import SimpleNamespace as NS
from contextlib import contextmanager
import pytest
from synvio.cloud import codex_workspace as codex


def test_workspace_contains_project_without_host_credentials():
    env=codex.environment({'index.html':'<h1>Hello</h1>'})
    assert env['container_size']=='small'
    assert 'env' not in env
    assert env['network']['access']=='restricted'
    assert env['files'][0]['path']=='/workspace/project.zip'
    assert codex.environment({},True)['desktop']=={'enabled':True}


def test_costs_are_provider_managed_and_unknown_spend_is_reserved():
    task={'model':'gpt-6.1-sol','model_budget_usd':.1}
    cost=codex.cost_record(task)
    assert cost['unresolved_reservation_usd']=='0.100000000'
    assert cost['calls'][0]['state']=='reserved'
    cost=codex.cost_record(task,{'input_tokens':1000,'output_tokens':100,'input_tokens_details':{'cached_tokens':0}},False)
    assert cost['estimated_cost_usd']=='0.003000000'
    assert float(cost['unresolved_reservation_usd'])==0


class Store:
    def __init__(self): self.values={}; self.events=[]; self.finished=None
    def write_cost(self,key,cost): self.cost=cost
    def fields(self,key,**values): self.values.update(values)
    def event(self,key,event): self.events.append(event)
    def put_json(self,kind,key,value): self.values[kind]=value
    def finish(self,task,files,status,report): self.finished=(files,status,report)


def fake_api(files):
    created=[]
    session=NS(id='sess_test',status='idle',usage=NS(model_dump=lambda:{'input_tokens':1000,'output_tokens':100,'input_tokens_details':{'cached_tokens':0}}))
    def create(**options): created.append(options); return session
    class Stream:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def iter_bytes(self): yield json.dumps(files).encode()
    artifacts=NS(list=lambda *a,**k:NS(data=[NS(id='art',turn_id='turn',path='/workspace/outputs/source.json',size_bytes=100)]),
                 with_streaming_response=NS(content=lambda *a,**k:Stream()))
    api=NS(beta=NS(agents=NS(sessions=NS(list=lambda **k:NS(data=[]),create=create,retrieve=lambda _:session,
        items=NS(list=lambda *a,**k:NS(data=[])),turns=NS(list=lambda *a,**k:NS(data=[NS(id='turn',status='completed')])),artifacts=artifacts))))
    @contextmanager
    def factory(): yield api
    return factory,created


def test_codex_imports_only_the_completed_source_artifact(monkeypatch):
    factory,created=fake_api({'index.html':'<h1>Updated</h1>'})
    monkeypatch.setattr(codex,'client',factory)
    store=Store()
    task={'id':'a','task':'Update the website','model':'gpt-6.1-sol','model_id':'codex:gpt-6.1-sol','model_budget_usd':.1}
    codex.run(store,task,{'index.html':'<h1>Old</h1>'})
    assert created[0]['spend_control']=={'limit':10}
    assert created[0]['agent']['service_tier']=='default'
    assert created[0]['extra_headers']['Idempotency-Key']=='a'
    assert store.values['codex_session_id']=='sess_test'
    assert store.finished[0]['index.html']=='<h1>Updated</h1>'
    assert store.finished[2]['browser_verified'] is False


def test_codex_cannot_publish_a_regressive_css_snapshot(monkeypatch):
    factory,_=fake_api({'style.css':'.logo:hover{color:blue}'})
    monkeypatch.setattr(codex,'client',factory)
    store=Store()
    task={'id':'a','task':'Add logo hover','model':'gpt-6.1-sol','model_id':'codex:gpt-6.1-sol','model_budget_usd':.1}
    with pytest.raises(ValueError,match='working project was not replaced'):
        codex.run(store,task,{'style.css':'\n'.join(f'.section-{i}{{color:white;padding:20px}}' for i in range(25))})
    assert store.finished is None
    assert 'candidate-files' in store.values
