"""Explain rate limits and prevent immediate paid restart loops."""
import time


def pause_details(error):
    text=str(error).casefold()
    if '429' in text or 'too many requests' in text or 'rate limit' in text:
        return {'failure_kind':'rate_limit','retry_after':time.time()+60,
                'pause_message':'A service is temporarily rate-limited. Saved work is safe. Wait before resuming; no automatic restart was made.'}
    return {}
