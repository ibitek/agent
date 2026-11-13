import json
from synvio.cloud.followups import context_for, prompt_for


def test_context_is_bounded_and_handles_cycles():
    tasks = {}
    for i in range(8):
        tasks[str(i)] = {"id": str(i), "task": "x" * 4000, "parent_id": str(i-1) if i else None, "status": "completed", "report": {"reason": "tests_passed", "attempt_details": [{"final_answer": "y" * 3000}]}}
    class Store:
        def get(self, value):
            return tasks.get(value)
    context = context_for(Store(), tasks["7"])
    assert len(context) == 3
    assert all(len(item["request"]) == 1200 and len(item["answer"]) == 800 for item in context)
    tasks["7"]["parent_id"] = "7"
    assert len(context_for(Store(), tasks["7"])) == 1


def test_current_request_preserved_and_history_is_marked_untrusted():
    task = {"task": "Make the button blue", "followup_context": [{"answer": "DONE", "status": "failed"}]}
    prompt = prompt_for(task)
    assert "untrusted historical context" in prompt
    assert prompt.endswith("Make the button blue")
    assert prompt_for({"task": "hello"}) == "hello"
