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
            raise StorageUnavailable("Podłącz Upstash Redis w zakładce Storage projektu Vercel.")
        self.url = (os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ["KV_REST_API_URL"]).rstrip("/")
        if not self.url.startswith("https://"):
            raise StorageUnavailable("Magazyn danych musi korzystać z HTTPS.")
        self.token = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ["KV_REST_API_TOKEN"]

    def call(self, *command):
        try:
            response = httpx.post(self.url, json=list(command), headers={"Authorization": f"Bearer {self.token}"}, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get("error"):
                raise StorageUnavailable("Magazyn danych odrzucił operację.")
            return data.get("result")
        except (httpx.HTTPError, ValueError) as exc:
            raise StorageUnavailable("Nie można połączyć się z magazynem danych.") from exc

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
        self.call("RPUSH", self.key("events", task_id), json.dumps(value, ensure_ascii=False, default=str))
        self.call("EXPIRE", self.key("events", task_id), RETENTION)
        self.fields(task_id, updated_at=time.time())

    def events(self, task_id, after=0):
        values = self.call("LRANGE", self.key("events", task_id), after, after + 499) or []
        return [{**json.loads(value), "sequence": after + i + 1} for i, value in enumerate(values)]

    def history(self, limit):
        ids = self.call("ZREVRANGE", self.key("history"), 0, limit - 1) or []
        return [task for task_id in ids if (task := self.get(task_id))]

    def put_json(self, kind, task_id, value, expiry=RETENTION):
        args = ["SET", self.key(kind, task_id), json.dumps(value, ensure_ascii=False)]
        if expiry:
            args += ["EX", expiry]
        self.call(*args)

    def get_json(self, kind, task_id, default=None):
        raw = self.call("GET", self.key(kind, task_id))
        return json.loads(raw) if raw else default

    def project(self):
        return self.get_json("project", "default", {"README.md": "# Nowy projekt SYNVIO\n\nTwórz kod i testy w tym projekcie.\n"})

    def meter(self, bucket, limit, seconds):
        count = self.call("EVAL", "local n=redis.call('INCR',KEYS[1]);if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end;return n", 1, self.key("quota", bucket), seconds)
        return int(count) <= limit

    def allow_paid_request(self):
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        limit = min(max(int(os.environ.get("SYNVIO_DAILY_REQUEST_LIMIT", "10")), 1), 100)
        return self.meter(f"paid:{day}", limit, 2 * 24 * 60 * 60)
