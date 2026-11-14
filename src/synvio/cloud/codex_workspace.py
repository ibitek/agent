"""Read-only Codex model checks and authorized workspace screenshots."""
import os
from fastapi import APIRouter
from .common import public_error
from .store import Store

router=APIRouter(prefix='/api')


def client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ.get('OPENAI_API_KEY'), base_url='https://api.openai.com/v1', max_retries=0, timeout=25)


@router.get('/codex/status')
def status():
    if not os.environ.get('OPENAI_API_KEY'):
        return {'available':False,'reason':'Configure the server OpenAI API key.'}
    models={}
    with client() as api:
        for name in ('gpt-6.1-sol','gpt-6-astra'):
            try:
                api.models.retrieve(name)
                models[name]={'available':True}
            except Exception as exc:
                models[name]={'available':False,'reason':public_error(exc)[:300]}
        result={'available':any(item['available'] for item in models.values()),'reason':None,
            'runtime':'Official Codex CLI in Vercel workspace','workspace':'Linux, Python, Node.js, terminal, files'}
    result['models']=models
    result['compute']='Disposable cloud compute is estimated separately. Every model call is checked against the task spending cap.'
    Store().put_json('codex-access','project',result,expiry=300)
    return result


@router.get('/tasks/{value}/codex-screen')
def screen(value:str):
    from .app import task_row
    store=Store();task_row(store,value)
    return store.get_json('codex-screen',value) or {'image':None,'updated_at':None}
