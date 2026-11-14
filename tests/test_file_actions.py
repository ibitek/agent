import json
import pytest
from synvio.cloud.file_actions import apply
from synvio.cloud.web_builder import build
from test_web_builder import Store, Engine


def test_batch_saves_related_files_in_one_model_turn():
    store=Store()
    build(store,{'id':'a','task':'Build a site','model':'fake'},{},Engine([
        {'action':'batch','actions':[{'action':'write','path':'index.html','content':'<h1>Hello</h1>'},
                                     {'action':'write','path':'style.css','content':'h1{color:blue}'}]},
        {'action':'finish','text':'Saved'}]))
    assert store.result['report']['steps_used']==2
    assert store.result['report']['tool_calls']==2
    assert store.saved['files','a']['style.css']=='h1{color:blue}'


def test_batch_is_atomic_when_a_later_patch_is_invalid():
    files={'index.html':'original'}
    with pytest.raises(ValueError):
        apply({'action':'batch','actions':[{'action':'write','path':'index.html','content':'changed'},
            {'action':'patch','path':'style.css','old':'missing','new':'anything'}]},files,'Edit the page')
    assert files=={'index.html':'original'}


def test_read_ranges_allow_access_to_long_minified_lines():
    files={'app.js':'x'*9000+'end'}
    _,_,out,_=apply({'action':'read','path':'app.js','start_char':8000},files,'Read it')
    result=json.loads(out)[0]
    assert result['content']=='x'*1000+'end' and result['truncated'] is False


def test_saved_file_body_is_not_sent_back_to_the_model_and_context_is_bounded():
    calls=[]
    class Capture(Engine):
        def generate(self,messages,**kwargs):
            calls.append([(str(m.role),m.content) for m in messages])
            return super().generate(messages,**kwargs)
    actions=[{'action':'write','path':f'note-{i}.txt','content':'UNIQUE_BODY_'+str(i)+'x'*3000} for i in range(12)]
    build(Store(),{'id':'a','task':'Save notes','model':'fake','runtime':'source'},{},Capture([
        *actions,{'action':'finish','text':'Saved'}]))
    assert max(map(len,calls))<=11
    assert 'UNIQUE_BODY_' not in json.dumps(calls)


def test_repeated_reads_without_progress_pause_instead_of_looping():
    store=Store()
    build(store,{'id':'a','task':'Review page','model':'fake'},{'index.html':'<h1>Saved</h1>'},Engine([
        {'action':'read','path':'index.html'}]*3))
    assert store.result['status']=='paused'
    assert store.result['report']['reason']=='no_progress'
    assert store.saved['files','a']['index.html']=='<h1>Saved</h1>'
