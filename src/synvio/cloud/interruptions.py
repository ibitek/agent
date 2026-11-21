"""Explain rate limits and prevent immediate paid restart loops."""
import time


class WorkPaused(RuntimeError):
    pass


def check_paused(store, task_id):
    if store.call('HGET', store.key('task', task_id), 'pause_requested') == 'true':
        raise WorkPaused('Paused at your request. Saved work can be resumed.')


def pause_details(error):
    text=str(error).casefold()
    if '429' in text or 'too many requests' in text or 'rate limit' in text:
        return {'failure_kind':'rate_limit','retry_after':time.time()+60,
                'pause_message':'A service is temporarily rate-limited. Saved work is safe. Wait before resuming; no automatic restart was made.'}
    return {}
