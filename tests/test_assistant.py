import pytest
from synvio.cloud.assistant import wants_changes
from synvio.cloud.assistant import confirmation


@pytest.mark.parametrize('text', ['to dawaj', 'yeah!', 'YES please', 'zrób to', 'Go ahead'])
def test_short_confirmations(text):
    assert confirmation(text)


@pytest.mark.parametrize('text', ["yeah but don't change files", 'not yet', 'yes, explain first'])
def test_conditional_messages_are_not_confirmation(text):
    assert not confirmation(text)


@pytest.mark.parametrize("message", ["Explain this project", "How do I fix this bug?", "Check the code without changing it", "Only review the code", "Do not change files", "hello", "Can you explain the implementation?", "Can you review how to fix this?", "Please explain how to add a button", "Could you inspect the changes we should make?"])
def test_read_only_requests_do_not_authorize_changes(message):
    assert not wants_changes(message)


@pytest.mark.parametrize("message", ["Build a calculator", "Please fix the broken button", "Can you add a dark theme?", "Make it blue", "Implement the design", "Zrob kalkulator"])
def test_explicit_change_requests_allow_execution(message):
    assert wants_changes(message)
