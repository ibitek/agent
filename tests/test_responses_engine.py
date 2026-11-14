from types import SimpleNamespace as NS
from openjarvis.core.types import Message, Role
from synvio.cloud.responses import ResponsesEngine
from synvio.cloud.metering import MeteredEngine


def test_modern_models_use_responses_and_meter_reasoning_output():
    calls=[]
    def create(**options):
        calls.append(options)
        return NS(id='response',output_text='{"action":"reply","text":"Done"}',usage=NS(input_tokens=1000,output_tokens=100,input_tokens_details=NS(cached_tokens=0)))
    engine=ResponsesEngine(NS(responses=NS(create=create)))
    meter=MeteredEngine(engine,'gpt-6.1-sol','.1')
    meter.generate([Message(role=Role.USER,content='Return JSON')],model='gpt-6.1-sol',max_tokens=2000,response_format={'type':'json_object'})
    assert calls[0]['text']=={'format':{'type':'json_object'}}
    assert calls[0]['max_output_tokens']==2000
    assert calls[0]['service_tier']=='default'
    assert 'temperature' not in calls[0]
    assert calls[0]['store'] is False
    assert meter.calls[0]['output_tokens']==100
