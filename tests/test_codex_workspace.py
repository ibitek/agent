from contextlib import contextmanager
from types import SimpleNamespace as NS
from synvio.cloud import codex_workspace as codex


def test_access_check_reads_model_metadata_without_running_an_agent(monkeypatch):
    checked=[];cached=[]
    @contextmanager
    def client():
        yield NS(models=NS(retrieve=lambda model:checked.append(model)))
    monkeypatch.setenv('OPENAI_API_KEY','test')
    monkeypatch.setattr(codex,'client',client)
    monkeypatch.setattr(codex,'Store',lambda:NS(put_json=lambda *a,**k:cached.append(a)))
    result=codex.status()
    assert result['available'] and checked==['gpt-6.1-sol','gpt-6-astra']
    assert result['runtime']=='Official Codex CLI in Vercel workspace'
    assert cached
