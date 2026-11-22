"""Versioned project files. Manual edits create reversible revisions, never AI calls."""
import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from pathlib import PurePosixPath
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from .common import MAX_PROJECT_FILES, safe_path, validate_files
from .image_generation import MODEL as IMAGE_MODEL
from .store import Store

router=APIRouter(prefix='/api/tasks')

def digest(files): return hashlib.sha256(json.dumps(files,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def source(store,value):
    from .app import task_row
    task=task_row(store,value)
    files=store.get_json('files',value)
    if files is None: files=store.get_json('preview-files',value)
    if files is None: files=store.get_json('baseline',value,{})
    return task,files

@router.get('/{value}/project-files')
def listing(value:str):
    store=Store();task,files=source(store,value)
    info=store.get_json('github-project',task.get('project_id') or task.get('thread_id',value)) or {}
    items=[{'path':name,'size_bytes':len(text.encode()),'retained':False} for name,text in sorted(files.items())]
    items += [{'path':item['path'],'retained':True,'reason':item['reason']} for item in info.get('retained',[]) if item['path'] not in files]
    return {'items':items, 'version':digest(files),'read_only':task['status'] in {'running','queued'}}

@router.get('/{value}/project-files/content')
def content(value:str,file:str=Query(min_length=1,max_length=200)):
    try: name=safe_path(file)
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    _,files=source(Store(),value)
    if name not in files: raise HTTPException(404,'File not found in this project revision.')
    return {'path':name,'content':files[name],'version':digest(files)}

@router.get('/{value}/revisions')
def revisions(value:str):
    store=Store();task,_=source(store,value);thread=task.get('thread_id',value)
    items=[item for item in store.history(200) if item.get('thread_id',item['id'])==thread and store.get_json('files',item['id']) is not None]
    return {'items':[{key:item.get(key) for key in ('id','task','created_at','status','operation')} for item in items]}


def require_head(store, task):
    if task['status'] in {'queued', 'running'}:
        raise HTTPException(409, 'Wait for the agent to finish before editing these files.')
    value = task['id']
    thread = task.get('thread_id', value)
    if store.get_json('thread-head', thread, value) != value:
        raise HTTPException(409, 'A newer revision exists. Open it before editing.')
    return thread


@contextmanager
def publish_revision(store, task, files, candidate, *, label, operation, image_paths=()):
    thread = require_head(store, task)
    value = task['id']
    new_id = uuid.uuid4().hex
    now = time.time()
    item = {**task, 'cost_record': None} if operation == 'manual_edit' else {
        key: task[key] for key in ('project_id', 'runtime', 'workspace', 'model_id', 'model', 'model_budget_usd') if key in task
    }
    item.update(id=new_id, parent_id=value, thread_id=thread, status='queued', task=label,
                operation=operation, created_at=now, updated_at=now, deadline=now + 60, report=None, error=None)
    if operation == 'generated_images':
        item.update(model_id='image:openai', model=IMAGE_MODEL, model_budget_usd=None)
    if not store.create(item):
        raise HTTPException(409, 'Project files are busy. Retry after the current operation finishes.')
    published = False
    try:
        current = store.get(value)
        if not current:
            raise HTTPException(409, 'The source revision expired before saving.')
        require_head(store, current)
        if operation == 'generated_images' and store.get_json('files', value) != files:
            raise HTTPException(409, 'The project files changed during image generation.')
        store.put_json('baseline', new_id, files)
        report = {
            'success': True, 'reason': operation, 'verification_level': 'files_only', 'browser_verified': False,
            'files_created': [path for path in candidate if path not in files],
            'files_modified': [path for path in candidate if path in files and candidate[path] != files[path]],
            'files_deleted': [path for path in files if path not in candidate],
            'attempt_details': [{'final_answer': 'Files saved as a new revision. The previous revision can be restored.'}],
            'tests': [],
        }
        store.finish(item, candidate, 'completed', report)
        published = True
        for path in image_paths:
            store.event(new_id, {'event': 'project_image_saved', 'path': path, 'success': True,
                                 'generated': operation == 'generated_images'})
        store.event(new_id, {'event': 'task_finished', 'status': 'completed', 'success': True})
        yield {'id': new_id, 'status': 'completed', 'version': digest(candidate)}
    except Exception:
        if not published:
            store.fields(new_id, status='failed', error='The file revision could not be published. The original revision is intact.')
        raise
    finally:
        store.release(new_id)


def check_image_destination(store, task, count, request_id):
    if not task:
        raise HTTPException(409, 'Open a saved project before generating an image into its files.')
    require_head(store, task)
    files = store.get_json('files', task['id'])
    if files is None:
        raise HTTPException(409, 'Open a saved revision with project files before generating an image.')
    if len(files) + count > MAX_PROJECT_FILES:
        raise HTTPException(409, 'There is no room for these images in the project file list.')
    directory = PurePosixPath('public/images' if task.get('runtime') == 'app' else 'images')
    info = store.get_json('github-project', task.get('project_id') or task.get('thread_id', task['id'])) or {}
    retained = {PurePosixPath(item['path']) for item in info.get('retained', [])}
    paths = [directory / f'kaelia-{request_id[:12]}-{index}.svg' for index in range(1, count + 1)]
    for path in paths:
        if str(path) in files or any(str(parent) in files for parent in path.parents):
            raise HTTPException(409, 'An image path is already occupied in the project.')
        if any(path == prior or prior in path.parents or path in prior.parents for prior in retained):
            raise HTTPException(409, 'A reserved GitHub path overlaps the generated image folder.')
    return files, paths


def save_generated_images(store, task, destination, images, size):
    files, paths = destination
    width, height = map(int, size.split('x'))
    added = {}
    for path, image in zip(paths, images):
        encoded = store.get_json('generated-image', image['id'])
        if not encoded:
            raise HTTPException(409, 'A generated image is temporarily unavailable for saving.')
        added[str(path)] = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<image width="{width}" height="{height}" href="data:image/jpeg;base64,{encoded}"/></svg>'
        )
    try:
        candidate = validate_files({**files, **added})
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    with publish_revision(store, task, files, candidate, label='Save generated images',
                          operation='generated_images', image_paths=added) as revision:
        return revision['id'], list(added)


class Edit(BaseModel):
    request_id:str=Field(pattern=r'^[a-f0-9]{32}$')
    expected_version:str=Field(pattern=r'^[a-f0-9]{64}$')
    operation:Literal['write','create','rename','delete','restore']
    file:str=Field(default='',max_length=200)
    target:str=Field(default='',max_length=200)
    content:str=Field(default='',max_length=262144)
    restore_id:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    conversation_id:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')

@router.post('/{value}/project-files')
def edit(value:str,body:Edit):
    store=Store();cached=store.get_json('file-edit',body.request_id)
    fingerprint=hashlib.sha256((value+body.model_dump_json()).encode()).hexdigest()
    if cached:
        if cached['fingerprint']!=fingerprint: raise HTTPException(409,'This edit identifier belongs to another change.')
        return cached['result']
    task,files=source(store,value)
    if task['status'] in {'running','queued'}: raise HTTPException(409,'Wait for the agent to finish before editing these files.')
    if digest(files)!=body.expected_version: raise HTTPException(409,'The files changed. Reload before saving.')
    info=store.get_json('github-project',task.get('project_id') or task.get('thread_id',value)) or {}
    retained={item['path'] for item in info.get('retained',[])}
    if body.file in retained or body.target in retained: raise HTTPException(409,'This path is retained in GitHub and is not editable in this workspace.')
    candidate=dict(files)
    try:
        if body.operation=='restore':
            other=store.get(body.restore_id or '')
            if not other or other.get('thread_id',other['id'])!=task.get('thread_id',value): raise ValueError('Choose a revision of this project.')
            candidate=store.get_json('files',body.restore_id)
            if candidate is None: raise ValueError('This revision has expired.')
        else:
            name=safe_path(body.file)
            if body.operation in {'write','create'}:
                if body.operation=='create' and name in candidate:
                    raise HTTPException(409,'A file already exists at this path. Choose another name.')
                candidate[name]=body.content
            else:
                if name not in candidate: raise ValueError('File not found.')
                if body.operation=='rename':
                    target=safe_path(body.target)
                    if target in candidate: raise ValueError('The destination file already exists.')
                    candidate[target]=candidate[name]
                del candidate[name]
        candidate=validate_files(candidate)
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    image_paths = (name,) if body.operation == 'create' and name.lower().endswith('.svg') and body.content.lstrip().startswith('<svg ') else ()
    with publish_revision(store, task, files, candidate, label='Manual '+body.operation+': '+body.file,
                          operation='manual_edit', image_paths=image_paths) as revision:
        result = {**revision, 'model_calls': 0}
        store.put_json('file-edit',body.request_id,{'fingerprint':fingerprint,'result':result})
        if body.conversation_id:
            conversation=store.get_json('conversation',body.conversation_id)
            if conversation and conversation.get('last_task_id')==value:
                now = time.time()
                conversation.update(last_task_id=revision['id'],updated_at=now)
                conversation['messages'].append({'id':body.request_id,'role':'assistant','kind':'manual_edit','content':'Project files saved as a new revision.','task_id':revision['id'],'created_at':now,'local_response':True})
                store.put_json('conversation',body.conversation_id,conversation)
        return result
