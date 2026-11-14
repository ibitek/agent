"""Task-scoped Responses proxy: the VM never receives an OpenAI credential."""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from decimal import Decimal
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from .common import public_error
from .metering import RATES, usd, usage_cost
from .retail import service_amount
from .store import Store

router=APIRouter(prefix='/api/codex-proxy')


def provision(store,task):
    token=secrets.token_urlsafe(32)
    store.fields(task['id'],codex_proxy_digest=hashlib.sha256(token.encode()).hexdigest())
    return token


def authorize(store,value,token):
    if not re.fullmatch('[a-f0-9]{32}',value):
        raise HTTPException(403,'Invalid task credential.')
    task=store.get(value)
    digest=hashlib.sha256(token.encode()).hexdigest()
    if not task or not hmac.compare_digest(digest,task.get('codex_proxy_digest','')):
        raise HTTPException(403,'Invalid task credential.')
    if task['status']!='running' or time.time()>task['deadline'] or not task.get('model_id','').startswith(('codex:','codex-browser:')):
        raise HTTPException(409,'This task is no longer accepting model requests.')
    return task


def estimate(task,payload):
    if not isinstance(payload,dict): raise HTTPException(422,'Invalid model request.')
    if payload.get('model')!=task['model'] or task['model'] not in RATES:
        raise HTTPException(422,'The proxy cannot change the task model.')
    if payload.get('previous_response_id'):
        raise HTTPException(422,'Explicit conversation input is required for budget checks.')
    tools=payload.get('tools') or []
    if not isinstance(tools,list) or any(not isinstance(t,dict) for t in tools):
        raise HTTPException(422,'Invalid tools.')
    if any(tool.get('type') not in {'function','custom'} for tool in tools):
        raise HTTPException(422,'This hosted tool has no approved rate in the proxy.')
    def check_inputs(value):
        if isinstance(value,dict):
            if value.get('type') in {'input_file','input_audio','input_video'}:
                raise HTTPException(422,'Only text and inline reference images are supported.')
            if value.get('type')=='input_image' and not str(value.get('image_url','')).startswith('data:image/'):
                raise HTTPException(422,'Reference images must be inline for budget checks.')
            for item in value.values(): check_inputs(item)
        elif isinstance(value,list):
            for item in value: check_inputs(item)
    check_inputs(payload.get('input'))
    limit=min(max(int(payload.get('max_output_tokens') or 4096),16),4096)
    data={**payload,'model':task['model'],'max_output_tokens':limit,'service_tier':'default','store':False,'stream':True,'max_tool_calls':1}
    # Inline image bytes are not text tokens. Reserve a conservative image bound.
    image_count=0
    def stripped(value):
        nonlocal image_count
        if isinstance(value,str) and value.startswith('data:image/'):
            image_count+=1
            return '[reference image]'
        if isinstance(value,dict): return {k:stripped(v) for k,v in value.items()}
        if isinstance(value,list): return [stripped(v) for v in value]
        return value
    incoming=len(json.dumps(stripped(data),ensure_ascii=False).encode())+2048+32768*image_count
    # Larger-context tiers are reserved at their higher rates before sending.
    factor=Decimal(2) if incoming>272000 else Decimal(1)
    rate_in,_,rate_out=RATES[task['model']]
    search=Decimal('.01') if any(t.get('type','').startswith('web_search') for t in tools) else Decimal(0)
    reserve=(incoming*rate_in*factor+limit*rate_out*(Decimal('1.5') if factor==2 else 1))/1_000_000+search
    return data,reserve


def record(task,calls,spent,reserved):
    budget=None if task['model_budget_usd'] is None else Decimal(str(task['model_budget_usd']))
    return {'event':'model_cost','currency':'USD','scope':'model_and_tools','rate_version':'openai-standard-2026-11-14',
        'budget_usd':usd(budget),'service_budget_usd':service_amount(budget),'estimated_cost_usd':usd(spent),
        'service_estimated_cost_usd':service_amount(spent),'unresolved_reservation_usd':usd(reserved),
        'service_reserved_usd':service_amount(reserved),'invoice_verified':False,'calls':calls}


@router.post('/{value}/responses')
async def response(value:str,request:Request):
    store=Store()
    token=request.headers.get('authorization','').removeprefix('Bearer ')
    task=authorize(store,value,token)
    raw=await request.body()
    if len(raw)>3*1024*1024: raise HTTPException(413,'The model request exceeds 3 MiB.')
    try: payload=json.loads(raw)
    except ValueError: raise HTTPException(422,'Invalid model request.') from None
    data,reserve=estimate(task,payload)
    lock=store.key('codex-inference-lock',value)
    lease=secrets.token_hex(16)
    if not store.call('SET',lock,lease,'NX','EX',110):
        raise HTTPException(409,'A model request for this task is already in flight.')
    def release():
        store.call('EVAL',"if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end",1,lock,lease)
    try:
        # Re-read after acquiring the lock: another request may have completed
        # while this request was reading its body.
        task=authorize(store,value,token)
        previous=task.get('cost_record') or {}
        spent=Decimal(previous.get('estimated_cost_usd','0'))
        unknown=Decimal(previous.get('unresolved_reservation_usd','0'))
        calls=list(previous.get('calls') or [])
        if unknown>0 or any(c.get('reservation_exceeded') for c in calls) or (task['model_budget_usd'] is not None and spent+reserve>Decimal(str(task['model_budget_usd']))):
            raise HTTPException(429,'Task spending limit reached, or previous provider usage is unresolved. No model call was sent.')
        entry={'sequence':len(calls)+1,'model':task['model'],'state':'reserved','reserved_usd':usd(reserve)}
        calls.append(entry)
        store.write_cost(value,record(task,calls,spent,reserve))
    except Exception:
        release();raise

    async def stream():
        completed=False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(85,connect=15)) as api:
                async with api.stream('POST','https://api.openai.com/v1/responses',json=data,
                        headers={'Authorization':'Bearer '+os.environ['OPENAI_API_KEY'],'Content-Type':'application/json'}) as upstream:
                    if 400<=upstream.status_code<500:
                        entry.update(state='rejected',provider_status=upstream.status_code)
                        store.write_cost(value,record(task,calls,spent,Decimal(0)))
                        completed=True
                        detail=(await upstream.aread())[:2000].decode('utf-8','replace')
                        text='OpenAI rejected the request with status '+str(upstream.status_code)+': '+public_error(ValueError(detail))[:800]
                        yield ('event: error\ndata: '+json.dumps({'type':'error','message':text})+'\n\n').encode()
                        return
                    upstream.raise_for_status()
                    async for line in upstream.aiter_lines():
                        if line.startswith('data: '):
                            try: event=json.loads(line[6:])
                            except ValueError: event={}
                            if event.get('type') in {'response.completed','response.incomplete'}:
                                result=event.get('response') or {}
                                usage=result.get('usage') or {}
                                cost,details=usage_cost(task['model'],{'prompt_tokens':usage.get('input_tokens'),
                                    'completion_tokens':usage.get('output_tokens'),'prompt_tokens_details':usage.get('input_tokens_details') or {}})
                                if details['input_tokens']>272000:
                                    ri,rc,ro=RATES[task['model']]
                                    cached=details['cached_input_tokens'] or 0
                                    cost=((details['input_tokens']-cached)*ri*2+cached*rc*2+details['output_tokens']*ro*Decimal('1.5'))/1_000_000
                                search_calls=sum(item.get('type')=='web_search_call' for item in result.get('output',[]))
                                cost+=Decimal('.01')*search_calls
                                entry.update(state='recorded',estimated_cost_usd=usd(cost),provider_response_id=result.get('id'),web_search_calls=search_calls,reservation_exceeded=cost>reserve,**details)
                                store.write_cost(value,record(task,calls,spent+cost,Decimal(0)))
                                completed=True
                        yield (line+'\n').encode()
        except Exception as exc:
            store.event(value,{'event':'proxy_error','error':public_error(exc)})
            yield ('event: error\ndata: '+json.dumps({'type':'error','message':'Model connection interrupted. Saved files are preserved; usage needs review.'})+'\n\n').encode()
        finally:
            try:
                if not completed:
                    entry['state']='unresolved'
                    store.write_cost(value,record(task,calls,spent,reserve))
            finally:
                release()
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
