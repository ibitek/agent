"""Persistent project index; old chat/run retention does not orphan latest files."""
import re
import time
import uuid
from fastapi import APIRouter, HTTPException
from .store import Store
router=APIRouter(prefix='/api/projects')

@router.get('')
def listing():
    store=Store();ids=store.call('ZREVRANGE',store.key('projects'),0,99) or []
    return {'items':[meta for value in ids if re.fullmatch(r'[a-f0-9]{32}',value) and (meta:=store.get_json('project-meta',value))]}

@router.post('/{value}/open')
def open_project(value:str):
    if not re.fullmatch(r'[a-f0-9]{32}',value):raise HTTPException(404,'Project not found.')
    store=Store();meta=store.get_json('project-meta',value)
    if not meta:raise HTTPException(404,'Project not found.')
    head=meta['head'];task=store.get(head)
    conversation=store.get_json('conversation',value)
    current=store.get(conversation.get('last_task_id','')) if conversation else None
    if current and current.get('project_id')==value:
        return {'conversation_id':value}
    if task and store.get_json('files',head) is not None:
        if conversation:
            conversation['last_task_id']=head
        else:
            conversation={'id':value,'project_id':value,'title':meta['title'],'messages':[],'last_task_id':head,'updated_at':time.time()}
        store.put_json('conversation',value,conversation)
        store.call('ZADD',store.key('conversations'),time.time(),value)
        return {'conversation_id':value}
    files=store.get_json('project',value)
    if files is None:raise HTTPException(404,'The latest project files are unavailable.')
    new_id=uuid.uuid4().hex;now=time.time()
    task={'id':new_id,'project_id':value,'thread_id':meta.get('thread_id',new_id),'parent_id':None,'runtime':meta.get('runtime','source'),'operation':'import','task':'Recover latest project: '+meta['title'],'model_id':'openai:low','model':'gpt-4o-mini','model_budget_usd':.1,'workspace':'project','created_at':now,'updated_at':now,'deadline':now+60,'status':'queued','report':None,'error':None}
    if not store.create(task):raise HTTPException(409,'Another project operation is active. Retry after it finishes.')
    try:
        report={'success':True,'reason':'latest_project_recovered','verification_level':'files_only','browser_verified':False,'files_created':[],'files_modified':[],'tests':[],'attempt_details':[{'final_answer':'Latest saved files recovered. Expired chat and tool history cannot be reconstructed. No model or code execution was started.'}]}
        store.finish(task,files,'completed',report)
        store.event(new_id,{'event':'task_finished','status':'completed','success':True})
        conversation={'id':value,'project_id':value,'title':meta['title'],'last_task_id':new_id,'updated_at':now,'messages':[{'id':new_id,'role':'assistant','kind':'import','content':'Your latest project files have been recovered.','task_id':new_id,'local_response':True,'created_at':now}]}
        store.put_json('conversation',value,conversation)
        store.call('ZADD',store.key('conversations'),now,value)
        return {'conversation_id':value}
    finally:store.release(new_id)
