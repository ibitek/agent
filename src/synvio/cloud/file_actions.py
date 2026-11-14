"""Bounded, atomic text-file operations. No generated commands run here."""
import json
from pathlib import PurePosixPath
from .common import safe_path, validate_files
from .edit_safety import check_replacement, patch_text


class UnsafeFileOperation(ValueError):
    pass


def operations(action):
    items=action.get('actions') if action.get('action')=='batch' else [action]
    if not isinstance(items,list) or not 1<=len(items)<=6 or any(not isinstance(item,dict) for item in items):
        raise UnsafeFileOperation('Use between one and six file operations per batch.')
    if any(item.get('action') not in {'list','read','write','patch','delete'} for item in items):
        raise UnsafeFileOperation('Unsupported website action. Only file operations can be batched.')
    return items


def apply(action,files,request,application=False,source_only=False):
    candidate=dict(files); changed=[]; results=[]; events=[]
    for item in operations(action):
        kind=item['action']
        if kind=='list':
            results.append({'files':sorted(candidate)});continue
        try: path=safe_path(item.get('path'))
        except ValueError as exc: raise UnsafeFileOperation(str(exc)) from exc
        before=candidate.get(path,'')
        if kind=='read':
            start=item.get('start_line',1); end=item.get('end_line',start+199) if type(start) is int else None
            if type(start) is not int or type(end) is not int or start<1 or end<start or end-start>199:
                raise ValueError('Read at most 200 lines using positive start_line and end_line.')
            lines=before.splitlines(keepends=True)
            offset=item.get('start_char',0)
            if type(offset) is not int or offset<0: raise ValueError('start_char must be a non-negative integer.')
            text=''.join(lines[start-1:end])[offset:]
            results.append({'path':path,'start_line':start,'total_lines':len(lines),
                'content':text[:8000] if path in candidate else 'File not found.',
                'next_start_char':offset+8000 if len(text)>8000 else None,
                'truncated':len(text)>8000 or end<len(lines)})
            events.append({'event':'tool_call','tool':'dev_read_file','path':path,'success':path in candidate})
            continue
        allowed={'.html','.css','.js','.svg','.json','.md','.txt'}|({'.jsx','.tsx','.ts','.mjs'} if application else set())
        if kind!='delete' and not source_only and PurePosixPath(path).suffix.lower() not in allowed:
            raise UnsafeFileOperation('Only supported text assets may be edited.')
        if kind=='delete':
            if path in candidate: changed.append(path)
            candidate.pop(path,None)
        else:
            content=patch_text(before,item.get('old'),item.get('new')) if kind=='patch' else item.get('content')
            if not isinstance(content,str): raise ValueError('File content must be text.')
            check_replacement(path,before,content,request)
            if path not in candidate or content!=before: changed.append(path)
            candidate[path]=content
        results.append({'path':path,'result':'removed' if kind=='delete' else 'saved','changed':path in changed})
        events.append({'event':'tool_call','tool':'dev_delete_file' if kind=='delete' else 'dev_write_file','path':path,'success':True})
    return validate_files(candidate),list(dict.fromkeys(changed)),json.dumps(results,ensure_ascii=False),events


def compact_action(action):
    """Do not send already-saved full source back as assistant output every turn."""
    # Never put an object/placeholder in a `content` field: a model may copy
    # that as its next write payload. Notes are clearly distinct from actions.
    return 'Previous operation summary (not an action schema; bodies omitted): '+ '; '.join(
        item['action']+' '+str(item.get('path','')) for item in operations(action))
