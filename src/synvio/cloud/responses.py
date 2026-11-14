"""Modern OpenAI models through Responses, without changing the external SDK."""
import os
from contextlib import contextmanager

MODELS = {'gpt-6.1-sol', 'gpt-6-astra'}


class ResponsesEngine:
    is_responses = True
    def __init__(self, client):
        self.client = client

    def generate(self, messages, *, model, max_tokens=1000, temperature=None, response_format=None):
        payload = []
        for message in messages:
            role = getattr(message.role, 'value', str(message.role))
            parts = [{'type':'output_text' if role == 'assistant' else 'input_text', 'text':message.content}]
            parts += [{'type':'input_image','image_url':image,'detail':'low'} for image in (getattr(message, 'images', None) or [])]
            payload.append({'role':role,'content':parts})
        options = {'model':model,'input':payload,'max_output_tokens':max_tokens,
                   'reasoning':{'effort':'low'},'service_tier':'default','store':False}
        if response_format:
            options['text'] = {'format':{'type':'json_object'}}
        response = self.client.responses.create(**options)
        usage = response.usage
        return {'content':response.output_text,'usage':{'prompt_tokens':usage.input_tokens,
            'completion_tokens':usage.output_tokens,'prompt_tokens_details':{'cached_tokens':usage.input_tokens_details.cached_tokens}},
            'response_id':response.id}


@contextmanager
def engine():
    from openai import OpenAI
    with OpenAI(api_key=os.environ.get('OPENAI_API_KEY'), base_url='https://api.openai.com/v1', max_retries=0, timeout=60) as client:
        yield ResponsesEngine(client)
