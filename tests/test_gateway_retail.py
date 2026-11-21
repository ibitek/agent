from decimal import Decimal
from types import SimpleNamespace
import httpx
import pytest

from synvio.cloud.gateway import GatewayEngine, SUPPORTED
from synvio.cloud.metering import MeteredEngine, BudgetExceeded
from synvio.cloud.retail import service_amount, compute_estimate


def test_service_estimates_are_separate_from_underlying_usage():
    assert Decimal(service_amount('0.10')) == Decimal('0.13')
    assert Decimal(compute_estimate(60)) == Decimal('0.00284')
    assert Decimal(compute_estimate(0)) == 0


def test_gateway_is_blocked_without_explicit_connection(monkeypatch):
    monkeypatch.delenv('SYNVIO_ENABLE_GATEWAY',raising=False)
    monkeypatch.setattr(httpx,'post',lambda *args,**kwargs:pytest.fail('No provider call expected'))
    with pytest.raises(ValueError,match='not connected'):
        GatewayEngine().generate([],model='google/gemini-2.5-flash')


def test_gateway_adapter_uses_fixed_endpoint_usage_and_existing_budget(monkeypatch):
    monkeypatch.setenv('SYNVIO_ENABLE_GATEWAY','1')
    monkeypatch.setenv('AI_GATEWAY_API_KEY','test-only-key')
    sent=[]
    def post(url,**kwargs):
        sent.append((url,kwargs))
        return httpx.Response(200,request=httpx.Request('POST',url),json={'choices':[{'message':{'content':'{"action":"reply","text":"Hello"}'}}],'usage':{'prompt_tokens':100,'completion_tokens':20}})
    monkeypatch.setattr(httpx,'post',post)
    model='mistral/codestral'
    meter=MeteredEngine(GatewayEngine(),model,'0.10')
    meter.generate([SimpleNamespace(role='user',content='Hi')],model=model,response_format={'type':'json_object'})
    assert len(sent)==1 and sent[0][0]=='https://ai-gateway.vercel.sh/v1/chat/completions'
    assert sent[0][1]['json']['model']==model
    assert Decimal(meter.summary()['service_estimated_cost_usd'])==Decimal(meter.summary()['estimated_cost_usd'])*Decimal('1.3')
    with pytest.raises(BudgetExceeded):
        MeteredEngine(GatewayEngine(),model,'0.000001').generate([],model=model)
    assert len(sent)==1


def test_gateway_does_not_enable_unhandled_tier_or_cache_write_rates():
    assert 'anthropic/claude-sonnet-4.5' not in SUPPORTED
    assert 'deepseek/deepseek-v3.2' not in SUPPORTED
    assert {'google/gemini-2.5-flash','mistral/codestral','alibaba/qwen3-coder'} <= set(SUPPORTED)


def test_existing_vercel_oidc_can_connect_without_new_gateway_key(monkeypatch):
    from synvio.cloud.gateway import connected, token
    monkeypatch.delenv('AI_GATEWAY_API_KEY',raising=False)
    monkeypatch.setenv('SYNVIO_ENABLE_GATEWAY','1')
    monkeypatch.setenv('VERCEL_OIDC_TOKEN','test-oidc-not-a-real-token')
    assert connected() and token()=='test-oidc-not-a-real-token'
