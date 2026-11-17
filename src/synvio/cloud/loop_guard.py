"""Durable bounded counters; changing a run ID must not reset failed repairs."""
import hashlib
import json

HARD_LIMIT = 6
MAX_PROBLEMS = 128
STRATEGY = ('Change strategy now: inspect the relevant saved source and the actual error. '
            'Do not repeat the same read, patch or finish claim. Use a different targeted '
            'read range or a smaller exact patch; preserve unrelated working code. '
            'Do not switch models, expand permissions or restart the task.')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class LoopGuard:
    def __init__(self, saved=None):
        saved = saved or {}
        self.seen = dict(saved.get('seen') or {})
        self.failures = dict(saved.get('failures') or {})
        if any(type(value) is not int or value < 1 for values in (self.seen, self.failures) for value in values.values()):
            raise ValueError('Invalid repair checkpoint. No model call was sent.')
        self.stopped = bool(saved.get('stopped')) or any(n >= HARD_LIMIT for n in [*self.seen.values(), *self.failures.values()])

    def snapshot(self):
        return {'version': 1, 'seen': self.seen, 'failures': self.failures, 'stopped': self.stopped}

    def _hit(self, counts, key):
        if key not in counts and len(counts) >= MAX_PROBLEMS:
            if counts is self.seen:
                # A large successful project is not a failed repair loop.
                # Drop an old one-off operation, preserving repeated problems.
                expendable = next((item for item, count in counts.items() if count == 1), None)
                if expendable is not None:
                    del counts[expendable]
            if len(counts) < MAX_PROBLEMS:
                return self._hit(counts, key)
            # Too many distinct unresolved issues also requires human review.
            self.stopped = True
            return HARD_LIMIT
        counts[key] = counts.get(key, 0) + 1
        if counts[key] >= HARD_LIMIT:
            self.stopped = True
        return counts[key]

    def operation(self, action, files, *, replay=False):
        from .file_actions import operations
        relevant = {}
        for item in operations(action):
            if item['action'] == 'list':
                relevant['file_map'] = sorted(files)
            else:
                path = item.get('path')
                relevant[path] = files.get(path)
        # An unrelated file change cannot disguise another identical read.
        key = digest([action, relevant])
        return self.seen[key] if replay and key in self.seen else self._hit(self.seen, key)

    def failure(self, category, reason):
        # Deliberately exclude model wording/patch fragments: changing an invalid
        # replacement must not reset the same underlying validation failure.
        return self._hit(self.failures, digest([category, ' '.join(str(reason).split())]))
