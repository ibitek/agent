"""Offline contract tests: no model, cloud service or generated code executes."""
import copy
import json
from types import SimpleNamespace as NS

import pytest
from openjarvis.core.types import Message, Role
from synvio.cloud.native_tools import NativeSession, NativeProtocolError, TOOL
from synvio.cloud.responses import ResponsesEngine
from synvio.cloud.metering import MeteredEngine, BudgetExceeded
from synvio.cloud.web_builder import build


def result(action, call_id='call_1', status='completed'):
    return {'content': '', 'response_status': status, 'native_output': [
        {'type': 'reasoning', 'id': 'reason', 'summary': [], 'encrypted_content': 'opaque'},
        {'type': 'function_call', 'name': 'workspace_action', 'call_id': call_id,
         'arguments': json.dumps({'operation': action})}]}


class Engine:
    supports_native_tools = True

    def __init__(self, actions):
        self.actions = iter(actions)
        self.calls = []

    def generate(self, messages, **options):
        self.calls.append(copy.deepcopy(options))
        assert 'response_format' not in options
        return result(next(self.actions), 'call_' + str(len(self.calls)))

    def summary(self):
        return {'estimated_cost_usd': '0'}


class Store:
    def __init__(self):
        self.snapshots = []
        self.events = []
        self.report = None

    def checkpoint(self, task_id, state, files):
        self.snapshots.append(copy.deepcopy((state, files)))

    def event(self, task_id, event):
        self.events.append(event)

    def finish(self, task, files, status, report):
        self.files, self.status, self.report = files, status, report


TASK = {'id': 'a', 'model': 'gpt-6-astra', 'task': 'Build a static page'}


def test_native_calls_are_executed_and_bound_to_their_real_outputs():
    engine = Engine([{'action': 'write', 'path': 'index.html', 'content': '<h1>Ready</h1>'},
                     {'action': 'finish', 'text': 'Saved.'}])
    store = Store()
    build(store, TASK, {'notes.txt': 'keep'}, engine)
    assert store.status == 'completed'
    assert store.files['notes.txt'] == 'keep'
    previous = engine.calls[1]['native_items']
    assert previous[0]['encrypted_content'] == 'opaque'
    assert previous[-1]['type'] == 'function_call_output'
    assert previous[-1]['call_id'] == previous[1]['call_id'] == 'call_1'
    assert 'saved' in previous[-1]['output']
    assert not any('encrypted_content' in json.dumps(event) for event in store.events)
    assert not store.report['browser_verified']


@pytest.mark.parametrize('bad', [
    result({'action': 'write', 'path': 'index.html', 'content': '<h1>Incomplete</h1>'}, status='incomplete'),
    result({'action': 'shell', 'command': 'whoami'}),
    result({'action': 'write', 'path': 'index.html', 'content': {'oops': True}}),
    {'content': '{"action":"write"}', 'response_status': 'completed', 'native_output': []},
])
def test_invalid_or_incomplete_native_response_pauses_without_retry_or_mutation(bad):
    engine = Engine([])
    calls = []
    def generate(*args, **kwargs):
        calls.append(1)
        return bad
    engine.generate = generate
    store = Store()
    build(store, TASK, {'index.html': '<h1>Keep</h1>'}, engine)
    assert calls == [1]
    assert store.status == 'paused'
    assert store.files == {'index.html': '<h1>Keep</h1>'}
    assert store.report['reason'] == 'native_protocol_error'


def test_multiple_native_calls_are_rejected_before_any_mutation():
    data = result({'action': 'list'})
    data['native_output'].append(copy.deepcopy(data['native_output'][-1]))
    with pytest.raises(NativeProtocolError, match='exactly one'):
        NativeSession().accept(data)


def test_pending_patch_recovers_from_checkpoint_without_another_paid_call():
    store = Store()
    original = {'index.html': '<h1>Old</h1>'}
    operation = {'action': 'patch', 'path': 'index.html', 'old': 'Old', 'new': 'New'}
    session = NativeSession()
    session.accept(result(operation, call_id='saved_call'))
    task = {**TASK, 'resume_checkpoint': {'pending_action': operation,
            'native_tool_state': session.state}}
    engine = Engine([{'action': 'finish', 'text': 'Updated.'}])
    build(store, task, original, engine)
    assert store.files['index.html'] == '<h1>New</h1>'
    assert len(engine.calls) == 1
    assert engine.calls[0]['native_items'][-1]['call_id'] == 'saved_call'
    assert store.report['success']


def test_validation_failure_returns_actual_result_to_finish_tool():
    store = Store()
    engine = Engine([{'action': 'finish', 'text': 'Done'},
                     {'action': 'write', 'path': 'index.html', 'content': '<h1>Now ready</h1>'},
                     {'action': 'finish', 'text': 'Saved'}])
    build(store, TASK, {}, engine)
    assert 'source check failed' in engine.calls[1]['native_items'][-1]['output']
    assert store.report['success']


def test_responses_wire_format_preserves_reasoning_and_uses_no_json_text_mode():
    from openai.types.responses import ResponseFunctionToolCall, ResponseReasoningItem
    calls = []
    data = result({'action': 'list'})
    def create(**options):
        calls.append(options)
        return NS(id='response', status='completed', output_text='',
                  output=[ResponseReasoningItem(**data['native_output'][0]),
                          ResponseFunctionToolCall(**data['native_output'][1])],
                  usage=NS(input_tokens=10, output_tokens=5, input_tokens_details=NS(cached_tokens=0)))
    engine = ResponsesEngine(NS(responses=NS(create=create)))
    meter = MeteredEngine(engine, 'gpt-6-astra', '2')
    response = meter.generate([Message(role=Role.USER, content='Build')], model='gpt-6-astra', max_tokens=100,
                              native_items=[])
    session = NativeSession()
    assert session.accept(response) == {'action': 'list'}
    session.complete('Files: index.html')
    meter.generate([Message(role=Role.USER, content='Continue')], model='gpt-6-astra',
                   max_tokens=100, native_items=session.input_items())
    assert calls[1]['input'][-3:] == session.input_items()
    assert calls[0]['tools'] == [TOOL]
    assert calls[0]['tool_choice'] == 'required'
    assert calls[0]['parallel_tool_calls'] is False
    assert calls[0]['include'] == ['reasoning.encrypted_content']
    assert calls[0]['store'] is False
    assert 'text' not in calls[0]
    assert meter.calls[0]['provider_response_id'] == 'response'


def test_native_history_and_tool_schema_are_included_in_budget_reservation():
    class MeterFake:
        supports_native_tools = True
        def generate(self, *args, **kwargs):
            raise AssertionError('Budget must reject before provider access')
    meter = MeteredEngine(MeterFake(), 'gpt-6-astra', '.01')
    with pytest.raises(BudgetExceeded, match='budget'):
        meter.generate([Message(role=Role.USER, content='Hi')], model='gpt-6-astra',
                       max_tokens=1, native_items=[{'type': 'reasoning', 'encrypted_content': 'x' * 2000}])
    assert not meter.calls


def test_native_tool_cannot_bypass_legacy_engine_capability_guard():
    meter = MeteredEngine(object(), 'gpt-4o-mini', '.2')
    with pytest.raises(BudgetExceeded, match='Native tools'):
        meter.generate([], model='gpt-4o-mini', native_items=[])


@pytest.mark.parametrize('checkpoint_number', [1, 2])
def test_crash_before_or_after_publishing_patch_resumes_without_duplicate_edit(checkpoint_number):
    class Crash(RuntimeError):
        pass
    class CrashStore(Store):
        def checkpoint(self, *args):
            super().checkpoint(*args)
            if len(self.snapshots) == checkpoint_number:
                raise Crash('Simulated worker shutdown')
    store = CrashStore()
    operation = {'action': 'patch', 'path': 'index.html', 'old': 'Old', 'new': 'New'}
    with pytest.raises(Crash):
        build(store, TASK, {'index.html': '<h1>Old</h1>'}, Engine([operation]))
    state, files = store.snapshots[-1]
    resumed = Store()
    engine = Engine([{'action': 'finish', 'text': 'Updated'}])
    build(resumed, {**TASK, 'resume_checkpoint': state}, files, engine)
    assert resumed.files == {'index.html': '<h1>New</h1>'}
    assert resumed.report['success']
    assert len(engine.calls) == 1


def test_deadline_preserves_pending_native_operation_for_next_resume():
    import time
    operation = {'action': 'patch', 'path': 'index.html', 'old': 'Old', 'new': 'New'}
    session = NativeSession()
    session.accept(result(operation))
    store = Store()
    build(store, {**TASK, 'deadline': time.time(), 'resume_checkpoint': {
        'pending_action': operation, 'native_tool_state': session.state}},
        {'index.html': '<h1>Old</h1>'}, Engine([]))
    state, files = store.snapshots[-1]
    assert state['pending_action'] == operation
    resumed = Store()
    build(resumed, {**TASK, 'resume_checkpoint': state}, files,
          Engine([{'action': 'finish', 'text': 'Updated'}]))
    assert resumed.report['success']


def test_native_checkpoint_cannot_be_replayed_with_another_model():
    with pytest.raises(ValueError, match='another model'):
        build(Store(), {**TASK, 'resume_checkpoint': {'native_tool_model': 'other'}}, {}, Engine([]))


def test_old_json_checkpoint_is_migrated_to_native_tools_without_losing_source():
    from synvio.cloud.web_builder import SYSTEM
    old = {'version': 1, 'changed': ['index.html'], 'pending_action': None,
           'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': 'Build a page'}]}
    store = Store()
    engine = Engine([{'action': 'finish', 'text': 'Saved'}])
    build(store, {**TASK, 'resume_checkpoint': old}, {'index.html': '<h1>Kept</h1>'}, engine)
    assert store.report['success']
    assert engine.calls[0]['native_items'] == []
    assert store.snapshots[-1][0]['native_tool_model'] == TASK['model']


def test_operation_limit_keeps_unexecuted_native_call_for_resume(monkeypatch):
    import synvio.cloud.web_builder as builder
    original_limits = builder.limits()
    monkeypatch.setattr(builder, 'limits', lambda: {**original_limits, 'tool_calls_per_task': 0})
    operation = {'action': 'write', 'path': 'index.html', 'content': '<h1>Saved once</h1>'}
    store = Store()
    engine = Engine([operation])
    build(store, TASK, {}, engine)
    state, files = store.snapshots[-1]
    assert files == {}
    assert state['pending_action'] == operation
    assert len(engine.calls) == 1
    monkeypatch.setattr(builder, 'limits', lambda: original_limits)
    resumed = Store()
    build(resumed, {**TASK, 'resume_checkpoint': state}, files,
          Engine([{'action': 'finish', 'text': 'Saved'}]))
    assert resumed.report['success']
    assert resumed.files == {'index.html': '<h1>Saved once</h1>'}


def test_native_batch_cannot_partially_publish_an_invalid_edit():
    store = Store()
    engine = Engine([{'action': 'batch', 'actions': [
        {'action': 'write', 'path': 'index.html', 'content': '<h1>New</h1>'},
        {'action': 'patch', 'path': 'style.css', 'old': 'missing', 'new': 'replacement'}]},
        {'action': 'finish', 'text': 'Nothing changed'}])
    build(store, TASK, {'index.html': '<h1>Keep</h1>'}, engine)
    assert store.files == {'index.html': '<h1>Keep</h1>'}
    assert 'all files unchanged' in engine.calls[1]['native_items'][-1]['output']

def test_native_workspace_does_not_resend_the_same_tool_result_as_chat():
    from types import SimpleNamespace
    from synvio.cloud.web_builder import native_messages

    def message(role, content):
        return SimpleNamespace(role=SimpleNamespace(value=role), content=content)

    output = "Untrusted tool result:\n" + "file contents " * 500
    messages = [message("system", "instructions"), message("user", "request"),
                message("user", "file map"), message("assistant", "read file"), message("user", output)]
    assert native_messages(messages, output) == messages[:3]
    assert native_messages(messages, "different result") == messages
    assert messages[-1].content == output
