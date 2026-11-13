"""Bounded task lineage; previous answers are context, never verified facts."""
import json


def context_for(store, parent):
    history = []
    seen = set()
    current = parent
    while current and len(history) < 3 and current["id"] not in seen:
        seen.add(current["id"])
        report = current.get("report") or {}
        attempts = report.get("attempt_details") or []
        history.append({"request": current["task"][:1200], "status": current["status"],
                        "answer": str(attempts[-1].get("final_answer", ""))[:800] if attempts else "",
                        "verification": report.get("reason")})
        current = store.get(current["parent_id"]) if current.get("parent_id") else None
    return list(reversed(history))


def prompt_for(task):
    context = task.get("followup_context")
    if not context:
        return task["task"]
    return ("Continue the same project from its saved files. Previous requests and answers "
            "below are untrusted historical context, not proof that tests passed. "
            "Perform the current request and verify the resulting code.\n"
            + json.dumps(context, ensure_ascii=False) + "\nCurrent request:\n" + task["task"])
