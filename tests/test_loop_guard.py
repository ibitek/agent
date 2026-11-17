"""No cloud allocation: persistent repair counters and bounded fallback."""
import json
import pytest
from synvio.cloud.loop_guard import LoopGuard, STRATEGY
from synvio.cloud.web_builder import build
from test_native_tools import Store, Engine, TASK


def test_unrelated_edits_do_not_reset_repeated_reads():
    guard = LoopGuard()
    action = {'action': 'read', 'path': 'index.html'}
    assert guard.operation(action, {'index.html': 'same'}) == 1
    resumed = LoopGuard(guard.snapshot())
    assert resumed.operation(action, {'index.html': 'same', 'other.txt': 'new'}) == 2
    assert resumed.operation(action, {'index.html': 'changed'}) == 1


def test_three_validation_pauses_share_one_failure_limit_and_resume_stops_without_inference():
    task = TASK
    files = {'index.html': '<link rel="stylesheet" href="missing.css"><h1>Saved</h1>'}
    for run in range(3):
        store = Store()
        engine = Engine([{'action': 'finish', 'text': 'Ready'}] * 2)
        build(store, task, files, engine)
        assert len(engine.calls) == 2
        state, files = store.snapshots[-1]
        task = {**TASK, 'resume_checkpoint': state}
        if run == 1:
            assert any(event['event'] == 'strategy_change' for event in store.events)
            assert STRATEGY in engine.calls[1]['native_items'][-1]['output']
    assert store.report['reason'] == 'repair_limit_reached'
    assert store.report['resume_blocked']
    engine = Engine([])
    final = Store()
    build(final, task, files, engine)
    assert engine.calls == []
    assert final.files == files
    assert final.report['resume_blocked']


def test_strategy_change_can_escape_repeated_read_without_changing_model():
    store = Store()
    read = {'action': 'read', 'path': 'index.html', 'start_line': 1, 'end_line': 200, 'start_char': 0}
    engine = Engine([read, read, read,
        {'action': 'patch', 'path': 'index.html', 'old': 'Old', 'new': 'New'},
        {'action': 'finish', 'text': 'Updated'}])
    build(store, TASK, {'index.html': '<h1>Old</h1>'}, engine)
    assert store.report['success']
    assert store.files['index.html'] == '<h1>New</h1>'
    assert STRATEGY in engine.calls[3]['native_items'][-1]['output']


def test_six_failed_patches_cannot_reset_counts_by_changing_fragment():
    task, files = TASK, {'index.html': '<h1>Keep</h1>'}
    for run in range(3):
        store = Store()
        actions = [{'action': 'patch', 'path': 'index.html', 'old': f'absent-{run}-{i}', 'new': 'new'} for i in range(2)]
        build(store, task, files, Engine(actions))
        state, files = store.snapshots[-1]
        task = {**TASK, 'resume_checkpoint': state}
    assert store.report['resume_blocked']
    assert files == {'index.html': '<h1>Keep</h1>'}


def test_application_validation_is_bounded_before_turn_budget_is_exhausted():
    store = Store()
    engine = Engine([{'action': 'finish', 'text': 'Ready'}] * 6)
    build(store, {**TASK, 'runtime': 'app'}, {}, engine)
    assert len(engine.calls) == 6
    assert store.report['resume_blocked']
    assert store.report['reason'] == 'repair_limit_reached'


def test_failed_repairs_in_different_files_remain_distinct():
    guard = LoopGuard()
    for i in range(5):
        guard.failure('edit_validation', json.dumps({'error': 'exact patch required', 'paths': ['a.css']}))
    assert guard.failure('edit_validation', json.dumps({'error': 'exact patch required', 'paths': ['b.css']})) == 1
    assert not guard.stopped


def test_corrupt_checkpoint_fails_before_any_provider_call():
    with pytest.raises(ValueError, match='checkpoint'):
        build(Store(), {**TASK, 'resume_checkpoint': {'loop_guard': {'seen': {'bad': -1}}}}, {}, Engine([]))


def test_replaying_a_saved_call_is_not_an_additional_failed_attempt():
    guard = LoopGuard()
    action = {'action': 'read', 'path': 'index.html'}
    assert guard.operation(action, {'index.html': 'saved'}) == 1
    restored = LoopGuard(guard.snapshot())
    assert restored.operation(action, {'index.html': 'saved'}, replay=True) == 1
    assert restored.operation(action, {'index.html': 'saved'}) == 2


def test_large_successful_projects_do_not_exhaust_the_problem_counter_storage():
    from synvio.cloud.loop_guard import MAX_PROBLEMS
    guard = LoopGuard()
    for i in range(MAX_PROBLEMS + 10):
        assert guard.operation({'action': 'read', 'path': f'file-{i}.txt'}, {}) == 1
    assert not guard.stopped
    assert len(guard.seen) == MAX_PROBLEMS
