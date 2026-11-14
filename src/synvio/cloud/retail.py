"""Service estimates only. No deposits, balance credits or payment capture."""
from decimal import Decimal, ROUND_CEILING

FACTOR = Decimal('1.30')
COMPUTE_PER_MINUTE = Decimal('0.005')


def service_amount(value):
    if value is None: return None
    return str((Decimal(str(value)) * FACTOR).quantize(Decimal('0.000000001'), rounding=ROUND_CEILING))


def compute_estimate(seconds):
    return str((Decimal(str(max(0, seconds))) * COMPUTE_PER_MINUTE / 60).quantize(Decimal('0.000000001'), rounding=ROUND_CEILING))
