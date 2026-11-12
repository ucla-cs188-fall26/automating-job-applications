from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from pocket_agent.forms.engine import UserAnswer, fill_form
from pocket_agent.forms.memory import Memory
from pocket_agent.forms.seed import seed_memory, eval_profile
from pocket_agent.resume_pdf import write_simple_pdf

URL = 'https://example.wd5.myworkdayjobs.com/en-US/External/job/Role/apply/autofillWithResume'


@pytest.mark.parametrize('failing, overlay', [(False, False), (False, True), (True, False)])
def test_workday_upload_waits_and_only_advances_after_success(tmp_path: Path, failing: bool, overlay: bool):
    resume = tmp_path / 'resume.pdf'
    write_simple_pdf(resume, ['Jordan Hale', 'Resume test'])
    memory = Memory(tmp_path / 'forms.sqlite')
    seed_memory(memory, eval_profile(), resume_path=str(resume))
    completion = "document.getElementById('status').textContent='Upload failed';document.getElementById('status').setAttribute('role','alert');" if failing else "document.getElementById('status').textContent=files[0].name+' Successfully Uploaded!';document.querySelector('button').disabled=false;"
    html = '''<h1>Autofill with Resume</h1><form onsubmit="event.preventDefault();window.nextClicks=(window.nextClicks||0)+1;document.body.innerHTML='<h1>My Information</h1><button type=submit>Submit Application</button>'">
      <div data-automation-id="file-upload-drop-zone"><div style="display:none"><input type=file></div>Drop your resume here</div>
      <div id=status></div><button type=submit disabled data-automation-id=bottom-navigation-next-button>Next</button></form>
      <script>document.querySelector('input').onchange=function(){
        window.uploads=(window.uploads||0)+1;window.uploadedName=this.files[0].name;
        const files=this.files;document.getElementById('status').textContent='Uploading';
        setTimeout(()=>{COMPLETION}, 1500);
      };</script>'''.replace('COMPLETION', completion)
    if overlay:
        html += """<script>
        const button = document.querySelector('button');
        button.setAttribute('aria-hidden','true');
        const container = document.createElement('div');
        container.style.cssText='position:relative;width:150px;height:40px';
        button.replaceWith(container);container.append(button);
        button.style.cssText='position:absolute;inset:0';
        const layer=document.createElement('div');layer.setAttribute('role','button');
        layer.setAttribute('aria-label','Next');layer.setAttribute('data-automation-id','click_filter');
        layer.style.cssText='position:absolute;inset:0;z-index:2';
        layer.onclick=()=>{if(!button.disabled)button.form.requestSubmit();};
        container.append(layer);
        </script>"""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html'))
            if failing:
                page.set_default_timeout(1000)
                # Use a bounded upload-wait timeout for the failure fixture.
                original = page.wait_for_function
                def short_wait(expression, **kwargs):
                    if kwargs.get('arg') == 'resume.pdf': kwargs['timeout'] = 2500
                    return original(expression, **kwargs)
                page.wait_for_function = short_wait
            result = fill_form(page, URL, memory, resume, lambda _: pytest.fail('No prompt expected'), None)
            assert page.evaluate('window.uploads') == 1
            assert page.evaluate('window.uploadedName') == 'resume.pdf'
            assert page.evaluate('window.nextClicks || 0') == (0 if failing else 1)
            assert result.submits == 0
            if failing:
                assert result.error and 'upload' in result.error.lower()
            else:
                assert not result.error
                assert page.locator('h1').inner_text() == 'My Information'
            browser.close()
    finally:
        memory.close()


def test_live_workday_layout_hidden_sibling_input_and_completed_step_label(tmp_path, monkeypatch):
    from pocket_agent.forms import engine
    from pocket_agent.forms.reader import extract_page

    resume = tmp_path / 'resume.pdf'
    write_simple_pdf(resume, ['Jordan Hale', 'Upload fixture'])
    memory = Memory(tmp_path / 'memory.sqlite')
    seed_memory(memory, eval_profile(), resume_path=str(resume))
    html = '''<div data-automation-id=progressBarActiveStep>current step 1 of 8 Autofill with Resume</div>
    <h3 id=title>Autofill with Resume</h3><div id=mount></div>
    <button type=submit data-automation-id=pageFooterNextButton disabled onclick="window.nextClicks=(window.nextClicks||0)+1; document.querySelector('[data-automation-id=progressBarActiveStep]').textContent='current step 2 of 8 My Information';document.getElementById('title').textContent='My Information';document.getElementById('mount').innerHTML='<label>First Name<input></label>';this.textContent='Submit Application';this.removeAttribute('data-automation-id');">Continue</button>
    <script>setTimeout(()=>{
      document.getElementById('mount').innerHTML='<div style="display:contents"><div data-automation-id=file-upload-drop-zone>Drop file here or <button type=button>Select file</button></div><input style="display:none" type=file data-automation-id=file-upload-input-ref></div><div id=status></div>';
      document.querySelector('input[type=file]').onchange=function(){window.uploads=(window.uploads||0)+1;document.getElementById('status').textContent=this.files[0].name+' Successfully Uploaded!';document.querySelector('[data-automation-id=pageFooterNextButton]').disabled=false;};
    },500);</script>
    <div>completed step 1 of 8 Autofill with Resume</div>'''
    # Reproduce the reader arriving before the hidden widget mounts.
    monkeypatch.setattr(engine, '_wait_ready', lambda page: extract_page(page))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html'))
            result = fill_form(page, URL, memory, resume, lambda _: pytest.fail('Unexpected prompt'))
            # This fixture ends at step 2 with no forward navigation.
            assert result.status == "blocked"
            assert result.current_step == "current step 2 of 8 My Information"
            assert "did not advance" in (result.error or "")
            assert page.evaluate('window.uploads') == 1
            assert page.evaluate('window.nextClicks') == 1
            assert page.locator('input').input_value() == 'Jordan'
            assert result.submits == 0
            browser.close()
    finally:
        memory.close()
