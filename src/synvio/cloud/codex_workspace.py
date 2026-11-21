"""Read-only Codex model checks and authorized workspace screenshots."""
import os
from fastapi import APIRouter
from .common import AUTO_CODEX_MODEL, public_error
from .store import Store

router=APIRouter(prefix='/api')


def client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ.get('OPENAI_API_KEY'), base_url='https://api.openai.com/v1', max_retries=0, timeout=25)


def automatic_edit_model(store):
    fallback = 'openai:low'
    if os.environ.get('SYNVIO_ALLOW_EXPENSIVE_MODELS') != '1' or not os.environ.get('OPENAI_API_KEY'):
        return fallback
    cached = store.get_json('codex-access', 'project') or {}
    model = (cached.get('models') or {}).get(AUTO_CODEX_MODEL)
    if model is None:
        from openai import APIError

        model = store.get_json('codex-access', 'auto')
    if model is None:
        try:
            with client() as api:
                api.with_options(timeout=5).models.retrieve(AUTO_CODEX_MODEL)
            model = {'available': True}
        except APIError:
            model = {'available': False}
        store.put_json('codex-access', 'auto', model, expiry=300)
    return 'codex:' + AUTO_CODEX_MODEL if model.get('available') else fallback


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
    result['compute']='Model and workspace estimates appear with each run.'
    Store().put_json('codex-access','project',result,expiry=300)
    return result


@router.get('/tasks/{value}/codex-screen')
def screen(value:str):
    from .app import task_row
    store=Store();task_row(store,value)
    return store.get_json('codex-screen',value) or {'image':None,'updated_at':None}
