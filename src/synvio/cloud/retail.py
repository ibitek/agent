"""Service estimates only. No deposits, balance credits or payment capture."""
from contextlib import asynccontextmanager, contextmanager
from decimal import Decimal, ROUND_CEILING
import time

FACTOR = Decimal('1.30')
CPU_PER_HOUR = Decimal('0.128')
MEMORY_PER_GB_HOUR = Decimal('0.0212')
SANDBOX_CREATION = Decimal('0.00000060')
COMPUTE_PER_MINUTE = (CPU_PER_HOUR + 2 * MEMORY_PER_GB_HOUR) / 60


def service_amount(value):
    if value is None: return None
    return str((Decimal(str(value)) * FACTOR).quantize(Decimal('0.000000001'), rounding=ROUND_CEILING))


def compute_estimate(seconds):
    return str((Decimal(str(max(0, seconds))) * COMPUTE_PER_MINUTE / 60).quantize(Decimal('0.000000001'), rounding=ROUND_CEILING))


@contextmanager
def track_compute(store, task_id, kind, time_limit):
    """Track elapsed time in the allocated 1 vCPU / 2 GB workspace."""
    field = 'compute_' + kind
    previous = (store.get(task_id) or {}).get(field) or {}
    started = time.time()
    state = {
        'task_id': task_id, 'started_at': started, 'time_limit': time_limit,
        'seconds': previous.get('seconds', 0) if previous.get('task_id') == task_id else 0,
        'sessions': 1 + (previous.get('sessions', 0) if previous.get('task_id') == task_id else 0),
    }
    store.fields(task_id, **{field: state}, updated_at=started)
    try:
        yield
    finally:
        now = time.time()
        state['seconds'] += min(time_limit, max(0, now - started))
        state['started_at'] = None
        store.fields(task_id, **{field: state}, updated_at=now)


def sandbox_usage(task, legacy_build_seconds=0, now=None):
    now = time.time() if now is None else now
    total = Decimal(0)
    active = False
    for kind in ('codex', 'build', 'tests'):
        state = task.get('compute_' + kind) or {}
        if state.get('task_id') != task.get('id') or not state:
            if kind == 'build':
                total += Decimal(compute_estimate(legacy_build_seconds))
            if kind == 'codex':
                total += Decimal(task.get('codex_compute_estimate_usd') or '0')
            continue
        seconds = state['seconds']
        if state['started_at'] is not None:
            elapsed = max(0, now - state['started_at'])
            seconds += min(state['time_limit'], elapsed)
            active |= elapsed < state['time_limit']
        total += Decimal(compute_estimate(seconds)) + state['sessions'] * SANDBOX_CREATION
    return {'service_estimated_cost_usd': service_amount(total), 'active': active}


@asynccontextmanager
async def track_async_compute(store, task_id, kind, time_limit):
    with track_compute(store, task_id, kind, time_limit):
        yield
