"""Builds use a fake SDK, never a real VM or paid model."""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from synvio.cloud.app_build import build, validate_app
from test_web_builder import Store

FILES = {'package.json':json.dumps({'scripts':{'build':'vite build'},'devDependencies':{'vite':'6.4.6'}}), 'index.html':'<div id="root"></div>'}


@pytest.mark.parametrize('files', [{}, {'package.json':'not JSON'}, {'package.json':'[]'}, {'package.json':'{}'}])
def test_invalid_application_never_reaches_vm(files):
    with pytest.raises(ValueError, match='Vite projects'):
        validate_app(files)


@pytest.mark.parametrize('failed', [False, True])
def test_isolated_build_network_files_credentials_and_cleanup(monkeypatch, failed):
    from vercel import sandbox
    import subprocess
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: pytest.fail('Host execution forbidden'))
    created, commands, policies, written, closed = [], [], [], {}, []
    class Reader:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def read(self, size):
            assert size == 2097153
            return json.dumps({'dist/index.html':'<h1>Built app</h1>'}).encode()
    class FS:
        async def mkdir(self, *args, **kwargs): pass
        @asynccontextmanager
        async def batch(self): yield self
        def write_text(self, path, content): written[path] = content
        def open(self, path, mode):
            assert path == '/vercel/sandbox/preview-artifacts.json' and mode == 'rb'
            return Reader()
    class Box:
        fs = FS()
        async def run_command(self, command, args, **kwargs):
            commands.append((command,args,kwargs))
            if kwargs.get('stdout'):
                kwargs['stdout'].write('actual build output')
            return SimpleNamespace(returncode=1 if failed and args[0]=='run' else 0)
        async def update(self, **kwargs): policies.append(kwargs)
    @asynccontextmanager
    async def create(**kwargs):
        created.append(kwargs)
        try: yield Box()
        finally: closed.append(True)
    monkeypatch.setattr(sandbox,'create_sandbox',create)
    store = Store()
    result = asyncio.run(build(store, 'task', 'request', FILES))
    assert result['status'] == ('failed' if failed else 'ready')
    assert closed == [True]
    assert created[0]['env'] == {} and created[0]['ports'] == []
    assert created[0]['persistent'] is False and created[0]['execution_time_limit'] == 160
    assert created[0]['resources'].vcpus == 1
    assert set(created[0]['network_policy'].allow) == {'registry.npmjs.org'}
    assert policies[0]['network_policy'].mode == 'deny-all'
    assert commands[0][0] == 'npm' and '--ignore-scripts' in commands[0][1]
    assert commands[1][2]['env'] == {}
    assert all(path.startswith('/vercel/sandbox/project/') for path in written)
    assert any(event.get('output') == 'actual build output' for event in store.events)
    if not failed:
        assert store.saved['built-files','task']['dist/index.html'] == '<h1>Built app</h1>'
        assert result['browser_verified'] is False
