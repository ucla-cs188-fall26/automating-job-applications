from playwright.sync_api import sync_playwright
from pocket_agent.forms.engine import fill_form, UserAnswer
from pocket_agent.forms.memory import Memory

URL='https://example.wd5.myworkdayjobs.com/en-US/External/job/Role/apply'


def test_workday_prompt_commits_selection_state_updates_and_continue_is_clicked(tmp_path):
    memory=Memory(tmp_path/'widgets.sqlite')
    memory.put('how_did_you_hear','LinkedIn',source='profile')
    memory.put('region','California',source='profile')
    html='''<div data-automation-id=progressBarActiveStep>current step 2 of 3 My Information</div><h3>My Information</h3>
    <form onsubmit="event.preventDefault();window.advanced=true;document.body.innerHTML='<h3>Review</h3><div data-automation-id=progressBarActiveStep>current step 3 of 3 Review</div><button type=submit>Submit Application</button>'">
    <div><label for=source>How Did You Hear About Us?</label><input id=source aria-required=true data-uxi-widget-type=selectinput enterkeyhint=search>
    <div role=listbox aria-label="items selected" id=tokens></div></div>
    <label for=state>State</label><button type=button aria-haspopup=listbox name=countryRegion id=state aria-label="State Texas Required" onclick="document.getElementById('options').hidden=false">Texas</button>
    <div role=listbox id=options hidden><div role=option onclick="document.getElementById('state').textContent='California';this.parentElement.hidden=true">California</div></div>
    <button type=submit data-automation-id=pageFooterNextButton>Save and Continue</button></form>
    <script>document.getElementById('source').onkeydown=function(e){if(e.key==='Enter'){e.preventDefault();document.getElementById('tokens').textContent=this.value;window.sourceCommitted=this.value;this.value='';}};</script>'''
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True);page=browser.new_page()
            page.route('**/*',lambda route:route.fulfill(body=html,content_type='text/html'))
            result=fill_form(page,URL,memory,None,lambda _:UserAnswer('',skip=True))
            assert page.evaluate('window.sourceCommitted')=='LinkedIn'
            assert page.evaluate('window.advanced') is True
            assert result.status=='review' and not result.error
            assert result.submits==0
            browser.close()
    finally:
        memory.close()
