import pytest
from playwright.sync_api import sync_playwright
from pocket_agent.forms.engine import fill_form, UserAnswer
from pocket_agent.forms.memory import Memory

URL='https://example.wd5.myworkdayjobs.com/en-US/External/job/Role/apply'


@pytest.mark.parametrize('missing', [False, True])
def test_workday_save_continue_moves_pages_and_stops_at_review(tmp_path, missing):
    memory=Memory(tmp_path/'memory.sqlite')
    if not missing:
        memory.put('city','Rosemead',source='profile')
    memory.put('first_name','Jordan',source='profile')
    html='''<div data-automation-id=progressBarActiveStep>current step 2 of 4 My Information</div>
    <h3 id=heading>My Information</h3><form onsubmit="event.preventDefault();window.nextClicks=(window.nextClicks||0)+1;
      if(window.nextClicks===1){document.querySelector('[data-automation-id=progressBarActiveStep]').textContent='current step 3 of 4 My Experience';document.getElementById('heading').textContent='My Experience';document.getElementById('fields').innerHTML='<label>First Name<input required></label>';}
      else{document.querySelector('[data-automation-id=progressBarActiveStep]').textContent='current step 4 of 4 Review';document.getElementById('heading').textContent='Review';document.getElementById('fields').innerHTML='';}">
    <div id=fields><label>City<input required></label></div>
    <button type=submit data-automation-id=pageFooterNextButton>Save and Continue</button></form>'''
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page()
            page.route('**/*',lambda route:route.fulfill(body=html,content_type='text/html'))
            result=fill_form(page,URL,memory,None,lambda _:UserAnswer('',skip=True))
            assert bool(result.error) is missing, result.error
            assert page.evaluate('window.nextClicks || 0')==(0 if missing else 2)
            assert result.submits==0
            assert page.locator('h3').inner_text()==('My Information' if missing else 'Review')
            browser.close()
    finally:
        memory.close()


def test_workday_multiline_progress_does_not_fake_a_step_change(tmp_path):
    memory=Memory(tmp_path/'multiline.sqlite')
    memory.put('city','Rosemead',source='profile')
    html='''<div data-automation-id=progressBarActiveStep>current step 2 of 3<br>My Information</div>
    <h3>My Information</h3><label>City<input required></label>
    <button type=submit data-automation-id=pageFooterNextButton onclick="window.nextClicks=(window.nextClicks||0)+1;setTimeout(()=>{document.body.innerHTML='<div data-automation-id=progressBarActiveStep>current step 3 of 3<br>Review</div><h3>Review</h3><button type=submit>Submit Application</button>';},1500)">Save and Continue</button>'''
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True);page=browser.new_page()
            page.route('**/*',lambda route:route.fulfill(body=html,content_type='text/html'))
            result=fill_form(page,URL,memory,None,lambda _:UserAnswer('',skip=True))
            assert page.evaluate('window.nextClicks')==1
            assert result.status=='review' and not result.error
            assert result.submits==0
            browser.close()
    finally:
        memory.close()
