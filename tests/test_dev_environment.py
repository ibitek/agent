import json
import time
from types import SimpleNamespace as NS
import pytest
from test_cloud_api import client, login
from synvio.cloud import dev_environment as dev
from synvio.cloud.store import Store
from synvio.cloud.app_build import validate_app
from synvio.cloud.visual_review import local_url, inspect


def test_starter_has_valid_vite_entry_exact_versions_and_official_component_license():
    from pathlib import Path
    files = dev.starter_files()
    validate_app(files)
    package = json.loads(files['package.json'])
    lock = json.loads(files['package-lock.json'])
    assert lock['packages']['']['dependencies'] == package['dependencies']
    assert all(not version.startswith(('^', '~', '*')) for version in package['dependencies'].values())
    assert 'MIT License' in files['LICENSE.shadcn.md']
    assert 'src/components/ui/button.jsx' in files
    assert not any('node_modules' in name or name.startswith('dist/') for name in files)
    source = Path(dev.__file__).with_name('starter')
    for name, content in files.items():
        if (source/name).is_file():
            assert (source/name).read_text(encoding='utf-8') == content


def test_larger_built_artifact_does_not_relax_source_file_limits():
    from synvio.cloud.common import validate_files
    artifact={'dist/app.js':'x' * 300000}
    with pytest.raises(ValueError, match='256 KiB'):
        validate_files(artifact)
    assert validate_files(artifact,max_file_bytes=512*1024)==artifact
    with pytest.raises(ValueError, match='512 KiB'):
        validate_files({'dist/app.js':'x'*600000},max_file_bytes=512*1024)


@pytest.mark.parametrize('baseline,task,expected', [
    ({'README.md':'keep'}, {'task':'Build a site','runtime':'web'}, True),
    ({'package.json':'{}'}, {'task':'Build a site','runtime':'app'}, False),
    ({'index.html':'working'}, {'task':'Build a site','runtime':'web'}, False),
    ({}, {'task':'Build using plain HTML','runtime':'web'}, False),
    ({}, {'task':'Build Python tool','runtime':'python'}, False),
])
def test_starter_never_replaces_an_existing_stack_or_explicit_static_request(baseline,task,expected):
    files, applied = dev.seed(task, baseline)
    assert applied is expected
    assert all(files[name] == content for name, content in baseline.items())
    if not applied: assert files == baseline


def test_environment_status_is_read_only_and_expired_images_are_not_ready(client):
    login(client)
    response=client.get('/api/codex/environment')
    assert response.status_code == 200 and not response.json()['prepared']
    store=Store()
    store.put_json('settings','developer-image', {'version':dev.VERSION,'expires_at':time.time()+3600,'snapshot_id':'cached'})
    assert client.get('/api/codex/environment').json()['prepared']
    store.put_json('settings','developer-image', {'version':'old','expires_at':time.time()+3600,'snapshot_id':'cached'})
    assert not client.get('/api/codex/environment').json()['prepared']


def test_managed_toolchain_does_not_expire_and_require_daily_user_installation(client):
    login(client)
    Store().put_json('settings','developer-image', {'version':dev.VERSION,'expires_at':0,'snapshot_id':'installed'})
    response=client.get('/api/codex/environment').json()
    assert response['prepared'] and response['managed'] and response['starter_ready']


@pytest.mark.parametrize('install_failure', [False, True])
def test_prepare_reuses_browser_snapshot_and_installs_tools_without_any_api_key(client, monkeypatch, install_failure):
    from synvio.cloud import browser_sessions
    from vercel import sandbox
    calls=[]
    class Files:
        async def mkdir(self,*a,**k): pass
        async def write_text(self,path,text): calls.append(('file',path))
        def batch(self): return Batch()
    class Batch:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        def write_text(self,path,text): calls.append(('file',path))
    class Box:
        name='coding-image';fs=Files()
        async def run_command(self,cmd,args,**kwargs):
            calls.append(('command',cmd,args,kwargs))
            if install_failure and args[0]=='ci':
                import subprocess
                raise subprocess.CalledProcessError(1, ['npm','ci'], output='private-model-key', stderr='NPM_INSTALL_DIAGNOSTIC')
            return NS(returncode=0)
        async def create_command(self,cmd,args,**kwargs): calls.append(('background',cmd,args,kwargs));return NS(id='server')
        async def update(self,**kwargs): calls.append(('policy',kwargs))
        async def snapshot(self,**kwargs): return NS(id='coding-snapshot')
        async def stop(self): calls.append(('stop',))
        async def destroy(self): calls.append(('destroy',))
    async def create(**kwargs): calls.append(('create',kwargs));return Box()
    async def prepared(): calls.append(('browser-prepared',))
    monkeypatch.setattr(browser_sessions,'prepare',prepared)
    monkeypatch.setattr(browser_sessions,'image',lambda store:{'snapshot_id':'browser-snapshot'})
    monkeypatch.setattr(sandbox,'create_sandbox',create)
    monkeypatch.setenv('OPENAI_API_KEY','private-model-key')
    login(client)
    response=client.post('/api/codex/environment/prepare')
    if install_failure:
        assert response.status_code==503
        assert 'NPM_INSTALL_DIAGNOSTIC' in response.text
        assert 'private-model-key' not in response.text
        assert ('destroy',) in calls
        assert not client.get('/api/codex/environment').json()['prepared']
        return
    assert response.status_code==200,response.text
    assert response.json()['prepared']
    assert calls[1][1]['env']=={} and calls[1][1]['ports']==[]
    assert 'private-model-key' not in repr(calls)
    commands=[item for item in calls if item[0]=='command']
    assert any('ci' in item[2] for item in commands)
    assert any(item[2]==['run','build'] for item in commands)
    count=len(calls)
    assert client.post('/api/codex/environment/prepare').json()['cached']
    assert len(calls)==count


@pytest.mark.parametrize('url',['https://example.com','file:///etc/passwd','http://localhost:5173','http://169.254.169.254','http://user:pass@127.0.0.1:5173'])
def test_render_helper_accepts_only_the_local_project_preview(url):
    with pytest.raises(ValueError): local_url(url)


def test_render_helper_collects_two_real_viewports_and_does_not_claim_interaction_testing(tmp_path):
    captures=[];viewports=[]
    class Page:
        def on(self,*args): pass
        def goto(self,*args,**kwargs): return NS(status=200)
        def evaluate(self,*args): return {'title':'App','textLength':150,'horizontalOverflow':False,'headings':1}
        def screenshot(self,**kwargs): captures.append(kwargs)
    class Context:
        def new_page(self): return Page()
        def route(self,*args): pass
        def close(self): pass
    class Browser:
        def new_context(self,**kwargs): viewports.append(kwargs['viewport']);return Context()
        def close(self): pass
    result=inspect(NS(chromium=NS(launch=lambda **kw:Browser())),'http://127.0.0.1:5173',tmp_path)
    assert viewports==[{'width':1440,'height':1000},{'width':390,'height':844}]
    assert len(captures)==2 and result['passed']
    assert result['interaction_tests_run'] is False and result['visual_review_required'] is True
    assert json.loads((tmp_path/'.kaelia/visual-review.json').read_text())['views'][1]['viewport']=='mobile'
