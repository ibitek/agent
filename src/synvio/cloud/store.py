"""Durable task state via Upstash Redis REST, never local SQLite in a Function."""

import json
import os
import time
from datetime import datetime, timezone

import httpx

from .common import JOB_SECONDS

PREFIX = "synvio:v1:"
RETENTION = 30 * 24 * 60 * 60


class StorageUnavailable(RuntimeError):
    pass


def ready() -> bool:
    return bool((os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL")) and (os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN")))


class Store:
    def __init__(self):
        if not ready():
            raise StorageUnavailable("Connect Upstash Redis in the project Storage settings.")
        self.url = (os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ["KV_REST_API_URL"]).rstrip("/")
        if not self.url.startswith("https://"):
            raise StorageUnavailable("Storage must use HTTPS.")
        self.token = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ["KV_REST_API_TOKEN"]

    def call(self, *command):
        try:
            response = httpx.post(self.url, json=list(command), headers={"Authorization": f"Bearer {self.token}"}, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get("error"):
                raise StorageUnavailable("Storage rejected the operation.")
            return data.get("result")
        except (httpx.HTTPError, ValueError) as exc:
            raise StorageUnavailable("Unable to connect to storage.") from exc

    def key(self, kind, value=""):
        return f"{PREFIX}{kind}:{value}"

    def fields(self, task_id, **values):
        encoded = []
        for name, value in values.items():
            encoded.extend((name, json.dumps(value, ensure_ascii=False)))
        if encoded:
            self.call("HSET", self.key("task", task_id), *encoded)

    def get(self, task_id):
        raw = self.call("HGETALL", self.key("task", task_id)) or []
        if not raw:
            return None
        if isinstance(raw, dict):
            return {name: json.loads(value) for name, value in raw.items()}
        return {raw[i]: json.loads(raw[i + 1]) for i in range(0, len(raw), 2)}

    def release(self, task_id):
        self.call("EVAL", "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end", 1, self.key("project-lock"), task_id)

    def create(self, task):
        if not self.call("SET", self.key("project-lock"), task["id"], "NX", "EX", JOB_SECONDS + 90):
            return False
        try:
            self.fields(task["id"], **task)
            self.call("EXPIRE", self.key("task", task["id"]), RETENTION)
            self.call("ZADD", self.key("history"), task["created_at"], task["id"])
            return True
        except Exception:
            self.release(task["id"])
            raise

    def transition(self, task_id, expected, status):
        return bool(self.call("EVAL", "if redis.call('HGET',KEYS[1],'status')==ARGV[1] then redis.call('HSET',KEYS[1],'status',ARGV[2],'updated_at',ARGV[3]);return 1 else return 0 end", 1, self.key("task", task_id), json.dumps(expected), json.dumps(status), json.dumps(time.time())))

    def event(self, task_id, payload):
        value = {**payload, "created_at": time.time()}
        self.call("EVAL", """
redis.call('RPUSH',KEYS[1],ARGV[1])
redis.call('EXPIRE',KEYS[1],ARGV[2])
redis.call('HSET',KEYS[2],'updated_at',ARGV[3])
return 1
""",2,self.key('events',task_id),self.key('task',task_id),json.dumps(value,ensure_ascii=False,default=str),RETENTION,json.dumps(value['created_at']))

    def events(self, task_id, after=0):
        values = self.call("LRANGE", self.key("events", task_id), after, after + 499) or []
        return [{**json.loads(value), "sequence": after + i + 1} for i, value in enumerate(values)]

    def history(self, limit):
        ids = self.call("ZREVRANGE", self.key("history"), 0, limit - 1) or []
        if not ids: return []
        rows=self.call('EVAL',"local rows={};for i,key in ipairs(KEYS) do rows[i]=redis.call('HGETALL',key) end;return rows",len(ids),
                       *(self.key('task',value) for value in ids)) or []
        return [{row[i]:json.loads(row[i+1]) for i in range(0,len(row),2)} for row in rows if row]

    def put_json(self, kind, task_id, value, expiry=RETENTION):
        args = ["SET", self.key(kind, task_id), json.dumps(value, ensure_ascii=False)]
        if expiry:
            args += ["EX", expiry]
        self.call(*args)

    def get_json(self, kind, task_id, default=None):
        raw = self.call("GET", self.key(kind, task_id))
        return json.loads(raw) if raw else default

    def project(self, project_id="default"):
        return self.get_json("project", project_id, {"README.md": "# New SYNVIO project\n\nBuild your application and its tests in this project.\n"})

    def write_cost(self, task_id, event):
        calls=event.get('calls') or []
        reserved=bool(calls and calls[-1].get('state')=='reserved')
        saved=self.call('EVAL', """
if ARGV[1]=='1' then
 local status=redis.call('HGET',KEYS[1],'status')
 local deadline=redis.call('HGET',KEYS[1],'deadline')
 if status~='"running"' or redis.call('GET',KEYS[2])~=ARGV[2] or (deadline and tonumber(deadline)<tonumber(ARGV[4])) then return 0 end
end
redis.call('HSET',KEYS[1],'cost_record',ARGV[3],'updated_at',ARGV[4])
return 1
""",2,self.key('task',task_id),self.key('project-lock'),'1' if reserved else '0',task_id,json.dumps(event),time.time())
        if not saved: raise RuntimeError('This worker no longer owns the run. No new model call was sent.')
        self.event(task_id,event)

    def checkpoint(self, task_id, payload, files):
        # Freeze a coherent file version and next action together. An expired or
        # paused worker cannot overwrite a checkpoint after ownership is lost.
        saved = self.call("EVAL", """
local status=redis.call('HGET',KEYS[1],'status')
local deadline=redis.call('HGET',KEYS[1],'deadline')
if status~='"running"' or (deadline and tonumber(deadline)<tonumber(ARGV[4])) then return 0 end
redis.call('SET',KEYS[2],ARGV[1],'EX',ARGV[3])
redis.call('SET',KEYS[3],ARGV[2],'EX',ARGV[3])
redis.call('SET',KEYS[4],ARGV[2],'EX',ARGV[3])
return 1
""", 4, self.key('task',task_id), self.key('checkpoint',task_id), self.key('files',task_id), self.key('preview-files',task_id), json.dumps(payload,ensure_ascii=False), json.dumps(files,ensure_ascii=False), RETENTION, time.time())
        if not saved:
            raise RuntimeError('Work paused: the worker no longer owns this run.')

    def finish(self, task, files, status, report):
        project_id=task.get('project_id') or 'default'
        previous=self.get_json('project-meta',project_id) or {}
        metadata={'id':project_id,'title':previous.get('title') or task.get('task','Project')[:80],'head':task['id'],'thread_id':task.get('thread_id',task['id']),'runtime':task.get('runtime','source'),'updated_at':time.time()}
        saved=self.call('EVAL', """
local status=redis.call('HGET',KEYS[1],'status')
local deadline=redis.call('HGET',KEYS[1],'deadline')
if (status~='"running"' and status~='"queued"') or redis.call('GET',KEYS[5])~=ARGV[1] or (deadline and tonumber(deadline)<tonumber(ARGV[5])) then return 0 end
redis.call('SET',KEYS[2],ARGV[2],'EX',ARGV[6])
redis.call('SET',KEYS[3],ARGV[2])
redis.call('SET',KEYS[4],ARGV[3],'EX',ARGV[6])
redis.call('HSET',KEYS[1],'status',ARGV[4],'report',ARGV[7],'updated_at',ARGV[5],'error','null')
redis.call('SET',KEYS[6],ARGV[8])
redis.call('ZADD',KEYS[7],ARGV[5],ARGV[9])
return 1
""",7,self.key('task',task['id']),self.key('files',task['id']),self.key('project',task.get('project_id') or 'default'),self.key('thread-head',task.get('thread_id',task['id'])),self.key('project-lock'),self.key('project-meta',project_id),self.key('projects'),task['id'],json.dumps(files,ensure_ascii=False),json.dumps(task['id']),json.dumps(status),time.time(),RETENTION,json.dumps(report,ensure_ascii=False),json.dumps(metadata,ensure_ascii=False),project_id)
        if not saved: raise RuntimeError('This run no longer owns the project. Its published revision was not changed.')

    def meter(self, bucket, limit, seconds):
        count = self.call("EVAL", "local n=redis.call('INCR',KEYS[1]);if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end;return n", 1, self.key("quota", bucket), seconds)
        return int(count) <= limit

    def allow_paid_request(self):
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        limit = min(max(int(os.environ.get("SYNVIO_DAILY_REQUEST_LIMIT", "100")), 1), 1000)
        # Fresh counter version excludes the old routing+worker double charges.
        return self.meter(f"paid-v2:{day}", limit, 2 * 24 * 60 * 60)
