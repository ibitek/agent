"""Modern OpenAI models through Responses, without changing the external SDK."""
import os
from contextlib import contextmanager

MODELS = {'gpt-6.1-sol', 'gpt-6-astra'}


class ResponsesEngine:
    is_responses = True
    supports_native_tools = True
    def __init__(self, client):
        self.client = client

    def generate(self, messages, *, model, max_tokens=1000, temperature=None, response_format=None, native_items=None):
        payload = []
        for message in messages:
            role = getattr(message.role, 'value', str(message.role))
            if role == 'assistant':
                payload.append({'role':role,'content':message.content})
                continue
            parts = [{'type':'input_text', 'text':message.content}]
            parts += [{'type':'input_image','image_url':image,'detail':'low'} for image in (getattr(message, 'images', None) or [])]
            payload.append({'role':role,'content':parts})
        options = {'model':model,'input':payload,'max_output_tokens':max_tokens,
                   'reasoning':{'effort':'low'},'service_tier':'default','store':False}
        if native_items is not None:
            from .native_tools import TOOL
            if response_format:
                raise ValueError('Native tools cannot be combined with legacy JSON mode.')
            options.update(tools=[TOOL], tool_choice='required', parallel_tool_calls=False,
                           include=['reasoning.encrypted_content'])
            payload.extend(native_items)
        elif response_format:
            options['text'] = {'format':{'type':'json_object'}}
        response = self.client.responses.create(**options)
        usage = response.usage
        result = {'content':response.output_text,'usage':{'prompt_tokens':usage.input_tokens,
            'completion_tokens':usage.output_tokens,'prompt_tokens_details':{'cached_tokens':getattr(usage.input_tokens_details, 'cached_tokens', None)}},
            'response_id':response.id}
        if native_items is not None:
            result.update(native_output=[item.model_dump(exclude_none=True) for item in response.output],
                          response_status=response.status)
        return result


@contextmanager
def engine():
    from openai import OpenAI
    with OpenAI(api_key=os.environ.get('OPENAI_API_KEY'), base_url='https://api.openai.com/v1', max_retries=0, timeout=60) as client:
        yield ResponsesEngine(client)
