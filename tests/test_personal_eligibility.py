from pocket_agent.forms.seed import seed_memory, eval_profile
from pocket_agent.forms.memory import Memory
from pocket_agent.forms.policy import ask_key
from pocket_agent.forms.engine import _ask_or_saved, UserAnswer
from types import SimpleNamespace


def test_residence_and_citizenship_are_separate(tmp_path):
    profile=eval_profile()
    profile['citizenship'].update(country='MM', permanent_residence=['US'])
    memory=Memory(tmp_path/'facts.sqlite')
    try:
        seed_memory(memory,profile)
        assert memory.get('auth_status').value=='Permanent Resident'
        assert memory.get('located_in_us').value=='Yes'
        assert memory.get('work_authorization').value=='Yes'
    finally:
        memory.close()


def test_personal_question_keys_keep_company_and_status_separate():
    assert ask_key('Do you have any family members that currently work for Zebra?')=='company.zebra.family_members'
    assert ask_key('Do you have any family members that currently work for Other Company?') is None
    assert ask_key('Do you have a non-compete?')=='non_compete'
    assert ask_key('Do you currently hold permanent residence or citizenship in another country?')=='other_residency_or_citizenship'


def test_exact_export_answer_not_reused_for_another_country_list(tmp_path,monkeypatch):
    profile=eval_profile()
    label='Export control: have you held citizenship in Myanmar or Cuba?'
    profile['form_answers']={'export_control':{'value':'Yes','always_use':True,'label':label}}
    memory=Memory(tmp_path/'exact.sqlite')
    try:
        # A previously learned label must not widen the scope of the new explicit answer.
        memory.put('export_control','No',label='Export control: have you held citizenship in Canada?')
        seed_memory(memory,profile)
        monkeypatch.setattr('pocket_agent.forms.engine._write',lambda page,field,value,resume:value)
        def field(text):
            return SimpleNamespace(label=text,section='',group=0,kind='text',value='')
        same=_ask_or_saved(None,field(label),memory,None,lambda _: (_ for _ in ()).throw(AssertionError('Should use saved answer')),'export_control')
        assert same.value=='Yes' and not same.asked
        prompts=[]
        def skip(item):
            prompts.append(item)
            return UserAnswer('',skip=True)
        other=_ask_or_saved(None,field('Export control: have you held citizenship in Canada?'),memory,None,skip,'export_control')
        assert prompts and other.action=='skip'
    finally:
        memory.close()
