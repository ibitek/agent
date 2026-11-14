"""Model-only cost estimates and conservative preflight budgets, not a wallet.

Rates verified 2026-11-13 against official model documentation. USD only.
Missing cache details mean an upper estimate, never a fabricated cache hit.
"""
import json
from decimal import Decimal, ROUND_CEILING
from types import SimpleNamespace
from .retail import service_amount
from .images import VISION_TOKENS, validate_images, require_vision

RATE_VERSION = "openai-standard-text-2026-11-13"
RATE_SOURCE = "https://developers.openai.com/api/docs/models/"
RATES = {
    "gpt-6.1-sol": (Decimal("2"), Decimal("0.10"), Decimal("10")),
    "gpt-6-astra": (Decimal("10"), Decimal("1"), Decimal("50")),
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.075"), Decimal("0.60")),
    "gpt-4o": (Decimal("2.50"), Decimal("1.25"), Decimal("10")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("0.10"), Decimal("1.60")),
    "gpt-4.1": (Decimal("2.00"), Decimal("0.50"), Decimal("8.00")),
}
from .gateway import rates as gateway_rates
RATES.update(gateway_rates())
MILLION = Decimal(1_000_000)
PRECISION = Decimal("0.000000001")


class BudgetExceeded(RuntimeError):
    pass


class ObservedClient:
    """Preserve raw usage the pinned SDK currently omits from its result."""
    def __init__(self, client):
        self.client = client
        self.usage = None
        self.response_id = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def __getattr__(self, name):
        return getattr(self.client, name)

    def create(self, **kwargs):
        # Never silently select a different, differently priced service tier.
        response = self.client.chat.completions.create(**{**kwargs, "service_tier": "default"})
        self.usage = response.usage.model_dump() if response.usage else None
        self.response_id = response.id
        return response


def amount(value):
    if value is None: return None
    number = Decimal(str(value))
    if not number.is_finite() or not Decimal("0") < number:
        raise ValueError("The AI budget must be a finite positive amount, or null for Unlimited.")
    return number


def usd(value):
    return None if value is None else str(value.quantize(PRECISION, rounding=ROUND_CEILING))


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError("The provider did not return valid usage data.")
    return value


def usage_cost(model, usage):
    incoming = count(usage.get("prompt_tokens"))
    outgoing = count(usage.get("completion_tokens"))
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens", usage.get("cached_tokens"))
    if cached is not None:
        cached = count(cached)
        if cached > incoming:
            raise ValueError("Invalid cached-token count.")
    rate_in, rate_cache, rate_out = RATES[model]
    cost = ((incoming - (cached or 0)) * rate_in + (cached or 0) * rate_cache + outgoing * rate_out) / MILLION
    return cost, {"input_tokens": incoming, "output_tokens": outgoing, "cached_input_tokens": cached, "cache_observed": cached is not None}


class MeteredEngine:
    """One sequential chat/task budget; persist reservations before inference.

    Preflight assumes no cache discount and at most one token per UTF-8 byte
    plus generous message framing. Only known text models/payloads supported.
    An ambiguous provider failure retains its reservation and stops this meter.
    Provider estimates remain separate from service-price estimates, infrastructure, tax and any future wallet ledger.
    """
    def __init__(self, engine, model, budget_usd, persist=lambda event: None):
        if model not in RATES:
            raise ValueError("No approved rate card exists for this model.")
        self.engine, self.model = engine, model
        self.budget = amount(budget_usd)
        self.spent = Decimal(0)
        self.reserved_unknown = Decimal(0)
        self.calls = []
        self.persist = persist
        self.blocked = False
        self.observed = None
        # Configure this SDK instance only; do not modify OpenJarvis files.
        inner = getattr(engine, "_inner", engine)
        client = getattr(inner, "_openai_client", None)
        if client is not None:
            if str(client.base_url).rstrip("/") != "https://api.openai.com/v1":
                raise ValueError("The budget rate card requires the standard OpenAI endpoint.")
            self.observed = ObservedClient(client.with_options(max_retries=0, timeout=60))
            inner._openai_client = self.observed

    def __getattr__(self, name):
        return getattr(self.engine, name)

    def summary(self):
        return {"currency": "USD", "scope": "model_only", "rate_version": RATE_VERSION,
                "blocked": self.blocked,
                "budget_usd": usd(self.budget), "estimated_cost_usd": usd(self.spent),
                "service_estimated_cost_usd": service_amount(self.spent),
                "service_budget_usd": service_amount(self.budget),
                "service_reserved_usd": service_amount(self.reserved_unknown),
                "unresolved_reservation_usd": usd(self.reserved_unknown),
                "invoice_verified": False, "calls": self.calls}

    def generate(self, messages, *, model, max_tokens=1024, **kwargs):
        if self.blocked or model != self.model or kwargs.keys() - {"temperature", "response_format"}:
            raise BudgetExceeded("Call blocked: the model, options or previous cost require review.")
        if "response_format" in kwargs and kwargs["response_format"] != {"type": "json_object"}:
            raise BudgetExceeded("Only the fixed JSON response format is supported by this budget guard.")
        messages = list(messages)
        payload = []
        image_tokens = 0
        image_payload = []
        for message in messages:
            content = message.content
            if not isinstance(content, str):
                raise BudgetExceeded("Budget checks currently support text messages only.")
            role = getattr(message.role, "value", str(message.role))
            payload.append({"role": role, "content": content})
            images = validate_images(getattr(message, "images", None) or [])
            require_vision(model, images)
            image_tokens += len(images) * VISION_TOKENS.get(model, 0)
            parts = ([{"type": "text", "text": content}] + [{"type": "image_url", "image_url": {"url": value, "detail": "low"}} for value in images]) if images else content
            image_payload.append({"role": role, "content": parts})
        if image_tokens and self.observed is None and not getattr(self.engine, "is_responses", False):
            raise BudgetExceeded("This engine does not support metered image inputs.")
        output_limit = count(max_tokens)
        if not 1 <= output_limit <= 16384:
            raise BudgetExceeded("Invalid response limit.")
        input_upper = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 1024 + 32 * len(payload) + image_tokens
        rate_in, _, rate_out = RATES[model]
        reservation = (input_upper * rate_in + output_limit * rate_out) / MILLION
        if self.budget is not None and self.spent + self.reserved_unknown + reservation > self.budget:
            raise BudgetExceeded("The AI budget has been reached. No further model call was sent.")
        entry = {"sequence": len(self.calls) + 1, "model": model, "state": "reserved", "reserved_usd": usd(reservation)}
        self.calls.append(entry)
        self.reserved_unknown += reservation
        # Persist first. Storage failure must prevent the provider request.
        self.persist({"event": "model_cost", **self.summary()})
        try:
            if self.observed:
                self.observed.usage = None
                self.observed.response_id = None
            if image_tokens and not getattr(self.engine, "is_responses", False):
                response = self.observed.create(model=model, messages=image_payload, max_tokens=output_limit, **kwargs)
                result = {"content": response.choices[0].message.content or "", "usage": self.observed.usage}
            else:
                result = self.engine.generate(messages, model=model, max_tokens=output_limit, **kwargs)
            if self.observed and self.observed.usage is not None:
                result = {**result, "usage": self.observed.usage}
            cost, usage = usage_cost(model, result.get("usage") or {})
        except Exception:
            self.blocked = True
            entry["state"] = "unresolved"
            self.persist({"event": "model_cost", **self.summary()})
            raise
        self.reserved_unknown -= reservation
        self.spent += cost
        entry.update(state="recorded", estimated_cost_usd=usd(cost), **usage)
        if self.observed:
            entry["provider_response_id"] = self.observed.response_id
        # Stop further calls if provider usage contradicts the preflight bound.
        if cost > reservation:
            self.blocked = True
            entry["preflight_exceeded"] = True
        self.persist({"event": "model_cost", **self.summary()})
        return result
