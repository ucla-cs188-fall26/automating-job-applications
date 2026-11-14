# Pocket Agent

Fill job applications in Chromium using your profile and resume, ask for missing answers, and leave the browser open for your review. **You submit the application yourself.**

The filler uses rules and saved answers first. GPT or Claude can draft written responses and research salary expectations when needed. Job search, fit scoring, and an application tracker are also available.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). From the project directory:

```bash
uv sync --extra dev
uv run playwright install chromium
uv run pocket-agent init --no-pull
```

`--no-pull` skips the older local-model download. You do not need Ollama or a GGUF file for the current filler. Run initialization once; it writes configuration.

## Set up your profile and resume

Start with the blank [profile.example.yaml](profile.example.yaml) skeleton, fill in your facts, and save it as **`~/.pocket-agent/profile.yaml`**. The root-level `profile.yaml` in your checkout is not the profile the program reads by default.

Replace the generated sample resume at **`~/.pocket-agent/resume.pdf`** with your actual resume. The installer creates fictional Jordan Hale sample files for first-time setup.

Keep education, work history, skills, links, and work authorization in the structured profile. Use `YYYY-MM` for education and employment dates. Resume parsing supplies recognized contact details; it does not populate every structured profile field.

For facts absent from your resume, use the `personal` section:

```yaml
personal:
  address_line_1: "YOUR STREET ADDRESS"
  city: "YOUR CITY"
  region: "YOUR STATE OR REGION"
  postal_code: "YOUR POSTAL CODE"
  country: "YOUR COUNTRY"
```

Put reusable application answers under `form_answers`. Sensitive answers require `always_use: true`; use wording that matches the site's options:

```yaml
form_answers:
  how_did_you_hear: Simplify
  race: {value: "YOUR ANSWER", always_use: true}
  hispanic: {value: "YOUR ANSWER", always_use: true}
  salary_expectation: {value: "YOUR ANNUAL BASE TARGET", always_use: true}
```

Explicit demographic answers are reused. Missing demographic answers are asked directly, even when the survey is voluntary. Identity is not inferred from your resume. Other unknown optional fields may remain unchanged.

## Run the filler

### Without AI

No API key is needed:

```bash
uv run pocket-agent fill 'APPLICATION_URL' --model none --interactive
```

### With GPT

Set these in the same terminal that launches the program:

```bash
export OPENAI_API_KEY='YOUR_API_KEY'
export POCKET_AGENT_AI_MODEL='YOUR_OPENAI_MODEL_ID'
export POCKET_AGENT_AI_MAX_CALLS=3

uv run pocket-agent fill 'APPLICATION_URL' --model gpt --interactive
```

`POCKET_AGENT_AI_MODEL` must be a model ID available to your API account. Despite its name, `--model gpt` selects the provider; the environment variable selects the actual model. GPT is the default provider when `--model` is omitted.

### With Claude

```bash
export ANTHROPIC_API_KEY='YOUR_API_KEY'
export POCKET_AGENT_AI_MODEL='YOUR_CLAUDE_MODEL_ID'

uv run pocket-agent fill 'APPLICATION_URL' --model claude --interactive
```

Environment settings last for the current terminal session. Missing API credentials or a missing model ID produces a configuration error.

### Local browser interface

```bash
uv run pocket-agent ui
```

The desk opens at `http://127.0.0.1:8765`. Paste a URL, answer questions in the interface, and manage your profile, resume, and saved answers. Select GPT, Claude, or None in Settings. Start the server from the terminal containing your API settings.

### During a run

1. The program opens the application, reads its fields, and fills known facts.
2. It uploads your resume and advances through supported Next/Continue controls.
3. Missing facts prompt you. Eligible written questions and salary fields can use AI.
4. After an answer, filling continues. Login, CAPTCHA, email verification, and unsupported controls may need manual browser work.
5. Review every value and submit manually.

In CLI interactive mode, type `/skip` at a field question to leave it blank. At a recovery prompt, fix the page and press Enter to continue, or type `q` to stop.

To upload a different resume for one CLI run:

```bash
uv run pocket-agent fill 'APPLICATION_URL' --model none --interactive --resume '/absolute/path/resume.pdf'
```

## When AI is used and what it costs

| Situation | Behavior |
| --- | --- |
| Known profile or saved answer | Fill directly; no API call |
| Simple supported Yes/No question about a listed skill | Answer from profile skills; no API call |
| Missing personal fact or preference | Ask you; no paid guess |
| Required written response | Ask AI to draft from resume/profile evidence; validate before filling |
| Required salary expectation without a reusable target | Research a new-grad annual base-salary range for the role/location and use its midpoint |
| Cached response for an identical request | Reuse it and validate again; no API call |
| Invalid AI output, exhausted budget, or API failure | Fall back to rules and user input |

Salary research requires provider-hosted web search support. The log shows the proposed amount, currency, range, and source links. Missing citations, old evidence, unclear context, or unsupported options cause a fallback to your input. A saved salary target avoids this research call.

**The default limit is 3 API requests per CLI fill run**, shared by writing and salary research. The desk applies this limit separately to each Start/Continue operation. Lower it before launching:

```bash
export POCKET_AGENT_AI_MAX_CALLS=1
```

The program stops further paid attempts after an API failure. The limit counts requests, not dollars; token usage and hosted searches affect the price. Use `--model none` to disable AI entirely.

Identical requests are cached locally for 24 hours. The key includes the provider, model, complete prompt and evidence, and generation settings. Changes to those inputs invalidate the cache. Generated responses are not promoted into reusable answer memory.

### Reading AI logs

Illustrative output:

```text
[AI] Request #1: gpt / YOUR_MODEL_ID — answer generation.
[AI] Response #1 received in 1.2s; checking whether it can be used.
[AI] Reusing cached response; no API request.
[AI] Requests attempted: 1; responses received: 1; completed: 1; failed: 0; cached: 1; skipped: 0.
```

Logs appear in the terminal or desk. A received response does not mean its answer was accepted or filled. Summary counts measure actual request attempts, not rule matches.

## Unattended mode

```bash
uv run pocket-agent fill 'APPLICATION_URL' --model gpt --unattended
```

This mode uses rules, saved answers, and eligible AI responses without terminal questions. Unresolved fields or browser gates stop progress. The browser stays open until you close it; add `--close-on-finish` to exit automatically, or `--headless` to run without a window.

Read `~/.pocket-agent/last-fill-report.json` for status, current step, unresolved questions, validation errors, and AI request counts:

| Status | Meaning |
| --- | --- |
| `review` | A review step was recognized with no known blockers |
| `blocked` | A gate, missing required answer, validation error, or execution error remains |
| `stopped` | Review was not established |

An unknown current step does not establish completion. Unattended exit codes are `0` for review, `2` for an incomplete run without an execution error, and `1` for an execution error or detected submission. Use `--interactive` to override a saved unattended setting.

## Workday accounts on macOS

General filling accepts application URLs from different platforms; support depends on their controls and layout. Workday has an additional opt-in account adapter:

```bash
uv sync --extra dev --extra accounts
uv run pocket-agent fill 'WORKDAY_JOB_URL' --model none --interactive --workday-accounts
```

It supports recognized employer URLs on `myworkdayjobs.com` and recruiting paths on `myworkdaysite.com`. It can create/sign into accounts using the resume/profile email and macOS Keychain credentials. Recognized registration consent is checked automatically. Optional `POCKET_AGENT_ACCOUNT_PASSWORD` supplies the new-account password; otherwise one is generated. No plaintext credential fallback is used.

CAPTCHA, email verification, MFA, SSO, and unsupported account layouts can require manual work. Custom domains are unsupported by the account adapter. Workday account/navigation tests use simulated forms; complete live signup compatibility is not established.

## Find jobs and track applications

These commands are separate from filling. Default feeds are Simplify's new-grad and Summer 2027 internship lists; Greenhouse and Lever boards are optional sources.

```bash
uv run pocket-agent fetch
uv run pocket-agent search software --location Austin
uv run pocket-agent job JOB_ID
uv run pocket-agent score JOB_ID
uv run pocket-agent track add --job-id JOB_ID --status saved
uv run pocket-agent track update --job-id JOB_ID --status applied
uv run pocket-agent track list
uv run pocket-agent track export applications.csv
```

Filling does not automatically mark a job as applied. Update the tracker after you submit.

## Data and privacy

The default data directory is `~/.pocket-agent/`; `POCKET_AGENT_HOME` overrides it.

| File | Purpose |
| --- | --- |
| `profile.yaml` | Applicant facts and reusable answers |
| `resume.pdf` | Default resume upload |
| `config.yaml` | Configuration, including CLI unattended/resume settings |
| `forms.sqlite` | Saved answers and learned field labels |
| `ai-cache.sqlite` | Cached AI response text and source URLs; may contain applicant data |
| `tracker.db` | Jobs and application statuses |
| `ui-settings.json` | Desk provider and browser settings |
| `last-fill-report.json` | Most recent unattended CLI report |

AI requests send question wording, relevant resume/profile evidence, and page context to the selected provider. API keys come from environment variables. `[AI]` request logs omit prompts and credentials; CLI fill summaries can print filled values. Form filling and resume uploads send applicant data to the employer's site.

Keep real profiles, resumes, reports, caches, and credentials out of Git. The bundled example applicant is fictional.

## Development and limitations

```bash
uv run pytest
uv run pocket-agent eval --suite smoke --backend mock --fail-under 1
uv run pocket-agent eval-forms --suite easy --model none --out form-eval.json
```

Focused hosted-AI and cost-control tests:

```bash
uv run pytest tests/test_ai_costs.py tests/test_cloud_forms.py tests/test_ai_answers.py tests/test_salary.py
```

Provider tests use mocked responses; passing them does not prove live API or employer-site compatibility. Current recovery tests include unresolved cases around invalid-answer retries and login/verification detection.

Browser navigation uses regular Python/Playwright code. AI answers questions; it does not plan arbitrary browser actions or repair unsupported widgets. Always inspect generated prose and final field values.

Older local-model chat, evaluation, training, snapshot, and MCP commands remain in the repository. Run `uv run pocket-agent --help` for the command inventory. Build history is in [docs/JOURNEY.md](docs/JOURNEY.md).

## License

[MIT](LICENSE). Hosted model/API usage is billed separately by the provider.
