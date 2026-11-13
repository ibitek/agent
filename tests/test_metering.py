"""Offline budget behavior; no inference, credentials or cloud allocations."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from synvio.cloud.metering import MeteredEngine, BudgetExceeded, usage_cost


class FakeEngine:
    def __init__(self, result=None, error=None):
        self.calls = 0
        self.result = result or {"content": "ok", "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}
        self.error = error

    def generate(self, *args, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


MESSAGES = [SimpleNamespace(role="user", content="hello")]


def test_cache_prices_and_missing_details_are_distinct():
    cost, usage = usage_cost("gpt-4o-mini", {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_tokens_details": {"cached_tokens": 800}})
    assert cost == Decimal("0.00015")
    assert usage["cached_input_tokens"] == 800
    cost, usage = usage_cost("gpt-4o-mini", {"prompt_tokens": 1000, "completion_tokens": 100})
    assert cost == Decimal("0.00021")
    assert usage["cached_input_tokens"] is None


def test_budget_rejects_before_provider_and_persistence_precedes_call():
    engine = FakeEngine()
    meter = MeteredEngine(engine, "gpt-4o-mini", "0.000001")
    with pytest.raises(BudgetExceeded):
        meter.generate(MESSAGES, model="gpt-4o-mini")
    assert engine.calls == 0
    states = []
    def persist(event):
        states.append((event["calls"][-1]["state"], engine.calls))
    meter = MeteredEngine(engine, "gpt-4o-mini", "0.1", persist)
    meter.generate(MESSAGES, model="gpt-4o-mini")
    assert states == [("reserved", 0), ("recorded", 1)]
    assert meter.summary()["estimated_cost_usd"] == "0.000210000"


def test_ambiguous_timeout_retains_reservation_and_never_retries():
    engine = FakeEngine(error=TimeoutError())
    meter = MeteredEngine(engine, "gpt-4o-mini", "0.1")
    with pytest.raises(TimeoutError):
        meter.generate(MESSAGES, model="gpt-4o-mini")
    assert Decimal(meter.summary()["unresolved_reservation_usd"]) > 0
    with pytest.raises(BudgetExceeded):
        meter.generate(MESSAGES, model="gpt-4o-mini")
    assert engine.calls == 1


def test_storage_failure_prevents_inference():
    def offline(event):
        raise RuntimeError("storage offline")
    engine = FakeEngine()
    meter = MeteredEngine(engine, "gpt-4o-mini", "0.1", offline)
    with pytest.raises(RuntimeError):
        meter.generate(MESSAGES, model="gpt-4o-mini")
    assert engine.calls == 0


@pytest.mark.parametrize("usage", [{}, {"prompt_tokens": -1, "completion_tokens": 2}, {"prompt_tokens": 3, "completion_tokens": 1, "cached_tokens": 4}])
def test_missing_or_invalid_usage_stops_further_paid_calls(usage):
    engine = FakeEngine({"usage": usage})
    meter = MeteredEngine(engine, "gpt-4o-mini", "0.1")
    with pytest.raises(ValueError):
        meter.generate(MESSAGES, model="gpt-4o-mini")
    with pytest.raises(BudgetExceeded):
        meter.generate(MESSAGES, model="gpt-4o-mini")


def test_no_unpriced_model_or_nonfinite_budget():
    with pytest.raises(ValueError):
        MeteredEngine(FakeEngine(), "unknown", "0.1")
    with pytest.raises(ValueError):
        MeteredEngine(FakeEngine(), "gpt-4o-mini", "NaN")


def test_pinned_sdk_adapter_captures_real_usage_shape_without_network():
    from openai.types.chat import ChatCompletion
    from openjarvis.engine.cloud import CloudEngine
    from openjarvis.core.types import Message, Role
    calls = []
    class Client:
        base_url = "https://api.openai.com/v1/"
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        def with_options(self, **kwargs):
            assert kwargs == {"max_retries": 0, "timeout": 60}
            return self
        def create(self, **kwargs):
            calls.append(kwargs)
            return ChatCompletion.model_validate({"id": "test-response", "object": "chat.completion", "created": 1, "model": "gpt-4o-mini", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100, "prompt_tokens_details": {"cached_tokens": 800}}})
    engine = CloudEngine.__new__(CloudEngine)
    engine._openai_client = Client()
    meter = MeteredEngine(engine, "gpt-4o-mini", "0.1")
    result = meter.generate([Message(role=Role.USER, content="hello JSON")], model="gpt-4o-mini", max_tokens=100, response_format={"type": "json_object"})
    assert result["usage"]["prompt_tokens_details"]["cached_tokens"] == 800
    assert meter.summary()["estimated_cost_usd"] == "0.000150000"
    assert meter.calls[0]["provider_response_id"] == "test-response"
    assert calls[0]["service_tier"] == "default"
    assert calls[0]["response_format"] == {"type": "json_object"}

@pytest.mark.parametrize('model,expected', [('gpt-4.1-mini', Decimal('0.00032')), ('gpt-4.1', Decimal('0.0016'))])
def test_added_models_charge_observed_cached_usage(model, expected):
    cost, usage = usage_cost(model, {'prompt_tokens':1000, 'completion_tokens':100, 'prompt_tokens_details':{'cached_tokens':800}})
    assert cost == expected
    engine = FakeEngine()
    with pytest.raises(BudgetExceeded):
        MeteredEngine(engine, model, '0.000001').generate(MESSAGES, model=model)
    assert engine.calls == 0
