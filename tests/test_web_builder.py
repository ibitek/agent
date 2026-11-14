import json
import pytest
from synvio.cloud.web_builder import build


class Store:
    def __init__(self):
        self.saved, self.events, self.result = {}, [], {}
    def put_json(self, kind, key, value, **kwargs):
        self.saved[kind, key] = value
    def event(self, key, value):
        self.events.append(value)
    def fields(self, key, **kwargs):
        self.result.update(kwargs)


class Engine:
    def __init__(self, actions):
        self.actions = iter(actions)
    def generate(self, *args, **kwargs):
        return {'content': json.dumps(next(self.actions))}
    def summary(self):
        return {'estimated_cost_usd': '0.0'}


def test_builder_can_finish_after_the_previous_ten_turn_limit():
    store=Store()
    actions=[{'action':'write','path':f'note-{i}.txt','content':'saved'} for i in range(11)]+[
        {'action':'write','path':'index.html','content':'<h1>Built</h1>'},
        {'action':'finish','text':'Saved'}]
    build(store,{'id':'a','task':'Build a page','model':'fake'},{},Engine(actions))
    assert store.result['report']['success']
    assert store.result['report']['steps_used']==13


def test_builder_preserves_files_before_execution_deadline():
    import time
    store=Store()
    build(store,{'id':'a','task':'Edit a page','model':'fake','deadline':time.time()+2},
        {'index.html':'<h1>Preserved</h1>'},Engine([]))
    assert store.result['status']=='paused'
    assert store.saved['files','a']['index.html']=='<h1>Preserved</h1>'


def test_web_builder_saves_live_preview_without_claiming_browser_tests():
    store = Store()
    engine = Engine([{'action': 'write', 'path': 'index.html', 'content': '<h1>Hello</h1>'}, {'action': 'finish', 'text': 'Page created.'}])
    build(store, {'id': 'a', 'task': 'Build a website', 'model': 'fake'}, {'notes.txt': 'keep'}, engine)
    report = store.result['report']
    assert report['success'] and report['verification_level'] == 'static'
    assert report['browser_verified'] is False and report['tests'] == []
    assert store.saved['preview-files', 'a']['notes.txt'] == 'keep'
    assert any(event['event'] == 'preview_updated' for event in store.events)


@pytest.mark.parametrize('path', ['../escape.html', '.env', 'server.py', '/index.html'])
def test_web_builder_rejects_unsafe_or_executable_host_files(path):
    store = Store()
    with pytest.raises(ValueError):
        build(store, {'id': 'a', 'task': 'Build a site', 'model': 'fake'}, {}, Engine([{'action': 'write', 'path': path, 'content': 'bad'}]))
    assert not store.saved


def test_web_builder_cannot_execute_commands():
    with pytest.raises(ValueError, match='Unsupported website action'):
        build(Store(), {'id': 'a', 'task': 'Build a site', 'model': 'fake'}, {}, Engine([{'action': 'shell', 'command': 'whoami'}]))


def test_react_source_is_not_falsely_reported_as_compiled():
    store = Store()
    engine = Engine([
        {'action':'write','path':'package.json','content':json.dumps({'scripts':{'build':'vite build'},'devDependencies':{'vite':'6.4.6'}})},
        {'action':'write','path':'index.html','content':'<div id="root"></div><script type="module" src="/src/main.jsx"></script>'},
        {'action':'write','path':'src/main.jsx','content':'import React from "react"; export default function App(){return <h1>Hello</h1>}'},
        {'action':'finish','text':'Saved source.'},
    ])
    build(store, {'id':'app','runtime':'app','task':'Build React application','model':'fake'}, {}, engine)
    report = store.result['report']
    assert report['success'] and report['verification_level']=='files_only'
    assert report['browser_verified'] is False and report['tests']==[]
    assert 'Compilation and browser tests were not run' in report['attempt_details'][0]['final_answer']
