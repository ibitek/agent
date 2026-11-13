import pytest
from fastapi import HTTPException
from synvio.cloud.editor import parse_action


@pytest.mark.parametrize("content", ['{"action":"shell","command":"whoami"}', '{"action":"read","path":"../outside"}', '{"action":"write","path":".env","content":"secret"}', 'not json', '{"action":"final","message":"done","command":"shell"}'])
def test_unknown_or_unsafe_actions_fail_closed(content):
    with pytest.raises(HTTPException):
        parse_action(content)


def test_known_actions_and_text_limits():
    assert parse_action('{"action":"read","path":"src/main.py"}')["path"] == "src/main.py"
    assert parse_action('{"action":"final","message":"done"}')["message"] == "done"
    import json
    with pytest.raises(HTTPException):
        parse_action(json.dumps({"action": "write", "path": "main.py", "content": "x" * 16001}))
