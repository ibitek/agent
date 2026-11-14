import pytest
from synvio.cloud.edit_safety import check_replacement, patch_text


def test_small_hover_change_cannot_delete_working_styles():
    before='\n'.join(f'.section-{i} {{ color:white; background:#15171b; padding:24px; }}' for i in range(20))
    with pytest.raises(ValueError):
        check_replacement('src/App.css',before,'.logo:hover {transform:scale(1.05)}','Add a small logo hover animation')
    after=patch_text(before,'.section-0 {','.logo:hover {transform:scale(1.05)}\n.section-0 {')
    check_replacement('src/App.css',before,after,'Add a small logo hover animation')
    assert '.section-19' in after


def test_explicit_rewrite_remains_possible_and_ambiguous_patch_is_rejected():
    check_replacement('src/App.css','a'*2000,'b','Rewrite this stylesheet from scratch')
    with pytest.raises(ValueError): patch_text('hello hello','hello','goodbye')
    with pytest.raises(ValueError): patch_text('hello','missing','goodbye')


def test_worker_can_fix_rejected_wholesale_write_using_patch():
    from synvio.cloud.web_builder import build
    from test_web_builder import Store, Engine
    css='\n'.join(f'.section-{i} {{ color:white; padding:24px; }}' for i in range(20))
    store=Store()
    build(store,{'id':'a','task':'Add a logo hover effect','model':'fake'}, {'index.html':'<h1>Hello</h1>','style.css':css},Engine([
        {'action':'write','path':'style.css','content':'.logo:hover{transform:scale(1.05)}'},
        {'action':'patch','path':'style.css','old':'.section-0 {','new':'.logo:hover{transform:scale(1.05)}\n.section-0 {'},
        {'action':'finish','text':'Updated hover.'}
    ]))
    assert '.section-19' in store.saved['files','a']['style.css']
    assert any(item['event']=='edit_rejected' for item in store.events)
