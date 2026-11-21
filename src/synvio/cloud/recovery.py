"""Explicit bounded continuation from saved work; never auto-repeat paid calls."""
import time
import hashlib
from decimal import Decimal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from .store import Store

router=APIRouter(prefix='/api/tasks')

class Resume(BaseModel):
    request_id: str=Field(pattern=r'^[a-f0-9]{32}$')
    model_budget_usd: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    acknowledge_unknown_cost: bool=False
    conversation_id: str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')

@router.post('/{value}/resume')
def resume(value:str,body:Resume):
    from .app import Task, create_task, task_row
    store=Store()
    fingerprint=hashlib.sha256((value+body.model_dump_json()).encode()).hexdigest()
    cached=store.get_json('resume-request',body.request_id)
    if cached:
        if cached['fingerprint']!=fingerprint: raise HTTPException(409,'This resume identifier belongs to another request or spending cap.')
        return cached['result']
    lock=store.key('resume-lock',value)
    if not store.call('SET',lock,body.request_id,'NX','EX',60): raise HTTPException(409,'This run is already being resumed.')
    try:
        task=task_row(store,value)
        if task.get('operation') in {'manual_edit','import'}: raise HTTPException(409,'Retry the original file or import operation. This operation must not start an AI worker.')
        if task['status'] not in {'failed','paused','cancelled'}: raise HTTPException(409,'Only interrupted work can be resumed.')
        checkpoint=store.get_json('checkpoint',value) or (store.get(value) or {}).get('resume_checkpoint')
        from .loop_guard import LoopGuard
        if (task.get('report') or {}).get('resume_blocked') or LoopGuard((checkpoint or {}).get('loop_guard')).stopped:
            raise HTTPException(409, 'The repair safety limit was reached. Saved files are preserved. Review the issue and send revised instructions instead of resuming the same attempts.')
        if task.get('retry_after',0)>time.time():
            raise HTTPException(429,'This service is cooling down. Saved work is preserved; wait before resuming.')
        if task.get('resumed_by'):
            child=store.get(task['resumed_by'])
            if not child: raise HTTPException(409,'The continuation has expired. Open the latest saved project.')
            if child['model_budget_usd']!=body.model_budget_usd: raise HTTPException(409,'A continuation already exists with a different spending cap. Open or cancel that run before starting another.')
            return {'source_id':value,'task':{'id':child['id'],'status':child['status']},'additional_model_cap_usd':child['model_budget_usd']}
        cost=task.get('cost_record') or {}
        if store.call('EXISTS', store.key('codex-inference-lock', value)):
            raise HTTPException(409, 'The previous model call is still settling. Wait a moment before resuming.')
        if Decimal(cost.get('unresolved_reservation_usd','0'))>0 and not body.acknowledge_unknown_cost:
            raise HTTPException(409,'A provider response was lost and its cost is unknown. Review usage, then acknowledge this before continuing. A new call may incur an additional charge.')
        files=store.get_json('files',value)
        if files is None: files=store.get_json('preview-files',value)
        if files is None: files=store.get_json('baseline',value)
        if files is None and task.get('parent_id'): files=store.get_json('files',task['parent_id'])
        if files is None: files=store.project(task.get('project_id') or 'default')
        thread=task.get('thread_id',value)
        head=store.get_json('thread-head',thread)
        if head and head not in {value,task.get('parent_id')}: raise HTTPException(409,'A newer project revision exists. Continue from that revision.')
        store.put_json('files',value,files)
        store.put_json('thread-head',thread,value)
        created=create_task(Task(images=store.get(value).get('images',[]),task=task['task'],parent_id=value,project_id=task.get('project_id'),runtime=task.get('runtime','python'),operation=task.get('operation','edit'),model_id=task['model_id'],model_budget_usd=body.model_budget_usd))
        new_id=created['id']
        if task.get('model_id','').startswith(('codex:','codex-browser:')):
            store.fields(new_id,codex_session_id=task.get('codex_session_id'),codex_submission=task.get('codex_submission') or task['id'],codex_resuming=True,codex_usage_before=task.get('codex_usage') or {})
        store.fields(new_id,resume_from=value,resume_checkpoint=checkpoint if task.get('runtime') in {'web','app','source'} else None)
        store.fields(value,resumed_by=new_id)
        if body.conversation_id:
            conversation=store.get_json('conversation',body.conversation_id)
            if conversation and conversation.get('last_task_id')==value:
                conversation['last_task_id']=new_id
                conversation['updated_at']=time.time()
                conversation['messages'].append({'id':body.request_id,'role':'assistant','content':'Resuming from the saved work. Completed changes are preserved.','kind':'execute','task_id':new_id,'created_at':time.time()})
                store.put_json('conversation',body.conversation_id,conversation)
        result={'source_id':value,'task':created,'checkpoint_restored':bool(checkpoint),'additional_model_cap_usd':body.model_budget_usd}
        store.put_json('resume-request',body.request_id,{'fingerprint':fingerprint,'result':result})
        store.event(new_id,{'event':'work_resumed','source_id':value,'checkpoint_restored':bool(checkpoint)})
        return result
    finally:
        store.call('EVAL',"if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end",1,lock,body.request_id)
