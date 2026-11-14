"""Image transport, cost reservation and recovery, with no paid requests."""
import base64
from types import SimpleNamespace
from decimal import Decimal

import pytest
from openai.types.chat import ChatCompletion
from openjarvis.core.types import Message, Role
from openjarvis.engine.cloud import CloudEngine
from pydantic import ValidationError

from synvio.cloud.assistant import Send
from synvio.cloud.images import validate_images
from synvio.cloud.metering import MeteredEngine, BudgetExceeded

PNG = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'x' * 100).decode()


@pytest.mark.parametrize('images', [['https://example.com/x.png'], [PNG]*3, ['data:image/svg+xml;base64,AAAA'], ['data:image/png;base64,a!'], ['data:image/png;base64,eA=='], ['data:image/png;base64,'+'A'*1_400_001]])
def test_rejects_remote_unsafe_invalid_and_oversize_images(images):
    with pytest.raises(ValidationError):
        Send(request_id='a'*32, message='Review this', images=images)


def client_engine(calls):
    class Client:
        base_url = 'https://api.openai.com/v1/'
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        def with_options(self, **options):
            assert options == {'max_retries':0, 'timeout':60}
            return self
        def create(self, **options):
            calls.append(options)
            return ChatCompletion.model_validate({'id':'image-test', 'object':'chat.completion','created':1,'model':'gpt-4o-mini','choices':[{'index':0,'message':{'role':'assistant','content':'{"action":"reply","text":"Image seen"}'},'finish_reason':'stop'}], 'usage':{'prompt_tokens':3000,'completion_tokens':10,'total_tokens':3010}})
    engine = CloudEngine.__new__(CloudEngine)
    engine._openai_client = Client()
    return engine


def test_image_is_transmitted_and_reserved_before_request():
    calls, states = [], []
    meter = MeteredEngine(client_engine(calls), 'gpt-4o-mini', '.01', lambda event: states.append((event['calls'][-1]['state'],len(calls))))
    result = meter.generate([Message(role=Role.USER,content='Inspect screenshot',images=[PNG])],model='gpt-4o-mini',max_tokens=100)
    assert states == [('reserved',0),('recorded',1)]
    assert calls[0]['messages'][0]['content'][1] == {'type':'image_url','image_url':{'url':PNG,'detail':'low'}}
    assert Decimal(meter.calls[0]['reserved_usd']) >= Decimal('0.00042495')
    assert result['usage']['prompt_tokens'] == 3000
    assert meter.calls[0]['input_tokens'] == 3000
    assert PNG not in str(meter.summary())


def test_small_budget_blocks_image_before_paid_call():
    calls = []
    meter = MeteredEngine(client_engine(calls),'gpt-4o-mini','.0001')
    with pytest.raises(BudgetExceeded):
        meter.generate([Message(role=Role.USER,content='Inspect',images=[PNG])],model='gpt-4o-mini',max_tokens=100)
    assert not calls


def test_builder_reattaches_image_after_checkpoint_restore():
    from synvio.cloud.web_builder import build
    from test_web_builder import Store
    class Engine:
        def generate(self,messages,**kwargs):
            assert messages[1].images == [PNG]
            return {'content':'{"action":"finish","text":"Reviewed"}'}
        def summary(self):
            return {}
    build(Store(),{'id':'a','task':'Repair website','model':'fake','images':[PNG],'resume_checkpoint':{'messages':[{'role':'system','content':'system'},{'role':'user','content':'Repair'}]}},{'index.html':'<h1>Hello</h1>'},Engine())


def test_truncated_json_is_not_executed_and_feedback_allows_recovery():
    from synvio.cloud.web_builder import build
    from test_web_builder import Store
    class Engine:
        def __init__(self):
            self.calls = 0
        def generate(self,messages,**kwargs):
            self.calls += 1
            if self.calls == 1:
                return {'content':'{"action":"write","path":"index.html","content":"partial'}
            assert 'truncated JSON' in str([m.content for m in messages])
            return {'content':'{"action":"write","path":"index.html","content":"<h1>Complete</h1>"}' if self.calls == 2 else '{"action":"finish","text":"Saved"}'}
        def summary(self):
            return {}
    store = Store()
    build(store,{'id':'a','task':'Build site','model':'fake'}, {},Engine())
    assert store.saved['files','a']['index.html'] == '<h1>Complete</h1>'
    assert store.result['report']['success']
    assert any(item['event']=='model_response_invalid' for item in store.events)
