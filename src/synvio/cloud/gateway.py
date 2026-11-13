"""Optional multi-provider adapter, with an explicit connection and pinned rates."""
import json
import os
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import httpx

CATALOG = json.loads(Path(__file__).with_name('gateway_catalog.json').read_text(encoding='utf-8'))
SUPPORTED = {item['id']:item for item in CATALOG['models'] if 'response_format' in (item.get('supported_parameters') or []) and not any(item['pricing'].get(key) for key in ('input_tiers','output_tiers','input_cache_write','varies_by_provider'))}


def connected():
    return os.environ.get('SYNVIO_ENABLE_GATEWAY') == '1' and bool(os.environ.get('AI_GATEWAY_API_KEY','').strip())


def rates():
    return {name:tuple(Decimal(item['pricing'][field])*1_000_000 for field in ('input','input_cache_read','output')) for name,item in SUPPORTED.items()}


class GatewayEngine:
    engine_id = 'synvio-gateway'
    is_cloud = True
    def generate(self, messages, *, model, max_tokens=1000, temperature=0.2, response_format=None):
        if model not in SUPPORTED or not connected():
            raise ValueError('This gateway model is not connected or approved.')
        payload = {'model':model,'messages':[{'role':getattr(message.role,'value',str(message.role)), 'content':message.content} for message in messages], 'max_tokens':max_tokens,'temperature':temperature}
        if response_format:
            payload['response_format'] = response_format
        response = httpx.post('https://ai-gateway.vercel.sh/v1/chat/completions', json=payload,
            headers={'Authorization':'Bearer '+os.environ['AI_GATEWAY_API_KEY']}, timeout=60)
        response.raise_for_status()
        data = response.json()
        content = data['choices'][0]['message'].get('content')
        if not isinstance(content, str):
            raise ValueError('Gateway returned no text response.')
        return {'content':content,'usage':data.get('usage',{}),'model':model}


@contextmanager
def engine_for(config):
    if config.model_override in SUPPORTED:
        yield GatewayEngine()
    else:
        from synvio.runtime import engine_for as existing_engine
        with existing_engine(config) as engine:
            yield engine
