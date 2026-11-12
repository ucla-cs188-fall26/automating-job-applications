# Pocket Agent, build journal

Times are America/Los_Angeles (PST in November). Nothing here is a guessed metric. If a number is in this file, it was measured on this machine or read from a response header, a model card, or `git log`.

The project is a fully local recruiting assistant. The longer bet is to distill a cloud agent into a small model that runs on a laptop, Apple Silicon first, and to publish the whole thing under MIT. The assistant finds public jobs, scores them against a local profile, tracks applications, and drafts answers. It never submits an application.

## 2026-11-03 16:20 PST — empty repo, written plan

The session started from an empty git repo whose only commit was `Initialize project` (16:20 PST). The source of truth was a short plan: four skills (find, screen, track, prefill), an OpenAI-compatible local backend, model tiers by RAM, a small Python harness, an MCP server, and an eval with programmatic checks. Week 1 was the working slice. Weeks 2–4 are synthetic profiles, teacher traces, LoRA, and an optional GRPO run. Training on real user data is out of scope.

The first decision was the layout. One installable Python package, `pocket-agent`, with the CLI of the same name. User files go in `~/.pocket-agent/`. The repository ships a fictional person, Jordan Hale at `jordan.hale@example.com`, phone `+1-555-0100`, Northwind University. A canary string, `POCKET-AGENT-CANARY-7f3a`, is written into profiles and traces so a real resume cannot be mixed into training data by accident.

## 2026-11-03 16:22 PST — which job feeds, and which ones actually answered

Public job APIs rot. Before writing a parser, the endpoints were requested for real.

- Greenhouse `https://boards-api.greenhouse.io/v1/boards/stripe/jobs` returned a JSON document with a `jobs` array (745 jobs in that pull).
- Lever `https://api.lever.co/v0/postings/netflix?mode=json` returned **404**. Spotify returned **200** at 00:22:10 GMT (`Content-Length: 820002`). Palantir also returned 200. Plaid, Rippling, Anduril, Shopify, Discord, and Figma returned 404. Company slugs are configuration, not something the client should guess.
- SimplifyJobs publishes `.github/scripts/listings.json` on the `dev` branch. A ranged GET of `SimplifyJobs/New-Grad-Positions` showed the object shape: `id`, `company_name`, `title`, `active`, `locations`, `url`, `sponsorship`, `degrees`, `date_posted`. A full download of that file was 13 MB and 19,044 rows, 3,115 of them `active`. `SimplifyJobs/Summer2027-Internships` has the same shape plus a `terms` array. The Summer 2026 repo is off-season. The live internship list is Summer 2027.

At 16:23 PST the feeds were narrowed: those two Simplify repos are the default, and `listings.json` is preferred over the README. Greenhouse and Lever stay optional, one company board at a time. That matches how people already use the Simplify lists, and it avoids pretending every ATS slug is stable.

An early read of `listings.json` through a web fetcher died at 5,000,000 bytes with `Unterminated string` (line 184800). The file is larger than that tool's cap. `curl` to disk got the full 13 MB. The client now downloads the file itself, with a six-hour cache, a one-second gap per host, and a normal User-Agent.

README parsing stayed as a fallback. The current Simplify README is an HTML table, not a pipe table, so both are supported. Sponsorship values seen in the new-grad file were mostly `Other` (18,967), plus `U.S. Citizenship is Required` (55), `Does Not Offer Sponsorship` (14), and `Offers Sponsorship` (8).

## Model tiers, checked rather than remembered

On 2026-11-04 the Ollama library pages for these tags returned HTTP 200: `qwen3:0.6b`, `qwen3:1.7b`, `qwen3:8b`, `qwen3:8b-q8_0`, `qwen3:14b`, `qwen3.5:4b`. The Hugging Face LICENSE files for `Qwen/Qwen3-0.6B`, `Qwen/Qwen3-1.7B`, `Qwen/Qwen3-14B`, and `Qwen/Qwen3.5-4B` start with the Apache License 2.0 text. LFM2.5 was named in the plan as an 8 GB alternative and was not pinned, because those Qwen tags were the ones whose license was actually opened.

`pocket-agent init` maps detected RAM at 12 GB and 24 GB:

| Detected RAM | Tier | Tag |
| --- | --- | --- |
| under 12 GB | 8 GB | `qwen3:1.7b` |
| 12 GB up to 24 GB | 16 GB | `qwen3.5:4b` |
| 24 GB and above | 32 GB | `qwen3:14b` |

This VM reported 15.6 GB, so init selected `qwen3.5:4b`. The CPU smoke model is `qwen3:0.6b` (about 523 MB on the Ollama page, 522,653,767 bytes once pulled). The 4B default was not pulled here. This box is a 4-core CPU with no GPU, which the Ollama installer also reported (`Unable to detect NVIDIA/AMD GPU`).

## 2026-11-03 16:41 PST — Week 1 is a package

Commit `Add Pocket Agent Week 1` landed at 16:41 PST. It has the CLI, profile schema, SQLite tracker with CSV export, the feed clients, nine tools, a newline-delimited MCP server, the agent loop, 50 hand-checked tasks, and GitHub Actions for Ubuntu and macOS. There is no `submit_application` tool. `db_upsert` only writes a local status. A unit test rejects a status of `submit`.

The mock suite, `pocket-agent eval --suite week1 --backend mock`, scored 50/50. Tool-call accuracy 1.0, JSON validity 1.0, restraint 1.0 on 18 tasks, latency 0.0 s because no model runs, peak RSS of that process 31.9 MB. `pytest` at that point was 16 passed, then 17 after an Ollama client test.

`pocket-agent synth --count 300 --seed 428` wrote `data/synthetic/profiles.jsonl` (300 lines, 336,549 bytes). Every row is `@example.com`, carries the canary, and leaves salary empty.

## Ollama, the empty `content` field, and a 0.6B model that copied the prompt

Installing Ollama needed `zstd` (`apt-get install zstd`). The install script then warned that systemd is not running, so the server was started by hand with `ollama serve`. `ollama pull qwen3:0.6b` finished, 522 MB of weights.

A call to `POST /v1/chat/completions` with `"think": false` came back in about 3.6 seconds with `content` equal to `""`, `completion_tokens` 120, and the actual text in `reasoning`. The same request on native `POST /api/chat` with `"think": false` returned `{"action":"final","answer":"hello"}` in `content`, `eval_count` 10, `eval_duration` 139528000 nanoseconds. Chat-template flags on the OpenAI shim (`chat_template_kwargs.enable_thinking`, `/no_think`) still left `content` empty. The CLI therefore talks to Ollama through `/api/chat`. llama.cpp and MLX stay on `/v1/chat/completions`.

Live `pocket-agent fetch` the same evening:

- Greenhouse `stripe`, limit 2: fetched 2, inserted 1, 1 duplicate.
- Lever `spotify`, limit 2: inserted 2.
- Simplify new-grad, limit 3, newest active rows: AMAX (Junior System Engineer, Fremont), Apple (Silicon Photonics Process Integration Engineer, Cupertino), Keysight (IC Test Engineer, Santa Rosa).

A frozen snapshot, `data/snapshots/public_postings.jsonl`, keeps the 1,000 newest active rows from each Simplify list. After dedupe that is **1,857** jobs (938 new-grad, 919 intern). Posted dates in the file run from **2026-10-05** through **2026-11-03**. The first version of `--limit` kept whatever came first in the file. It was changed, before this snapshot was written, to sort by `posted_at` descending. In this slice, `citizenship_required` and `clearance_required` are both 0, and every row's sponsorship is `other`. Degree lists are populated: 88 rows are PhD only. Eligibility traps for this snapshot have to use degree and graduation timing, not clearance flags that are not in the file.

The first CPU smoke, four tasks, still had the model echoing format placeholders (`<text>`, `<question>`). Score: **0/4** task success, tool-call accuracy 0.50, JSON validity 1.0, restraint 0.0, latency p50/p95 **0.383 s / 0.717 s**, eval-process peak RSS 36.4 MB. Sampling RSS by the process name `ollama` showed 41.7 MB. That number is the parent. The weights were in a child named `llama-server` at about 1.07 GB RSS (`1095472` kB in `/proc`). The eval reporter was changed to record that child. The placeholder text was removed from the prompt and replaced with a concrete sentence.

The full 50-task run after that change, label `CPU smoke test`, model `qwen3:0.6b`:

| | |
| --- | --- |
| Task success | **2/50 (0.04)** |
| Tool-call accuracy | 0.40 |
| JSON validity | 1.0 |
| Restraint | 2/18 (0.111) |
| Latency p50 / p95 | 0.449 s / 0.622 s |
| Eval process peak RSS | 39.2 MB |
| llama-server RSS | 1112.5 MB |
| Completion tokens | 1080 |
| Tokens/s | 45.1 |

The two passes were ASK_USER turns: salary expectations, and notice period. JSON parsed every time. The usual wrong answer was the example sentence that had just been added to the prompt: "Contoso Robotics is an eligible new-grad match." The model said that and called no tool. Putting a fluent example in the prompt taught a 0.6B model to copy it. That is the failure the next harness change is aimed at. The 2/50 table above is the before number.

## 2026-11-03 16:46 PST — commits, identity, and a push that did not happen

Five commits at 16:46 PST record the native Ollama client, newest-first limits, llama-server RSS, the snapshot, and the baseline in the README. `git config` on this clone is `Linn Khant Thuya <68958137+linnkhant07@users.noreply.github.com>`. The two earlier commits were rewritten to the same author and committer before any push, so a future GitHub history is not attributed to the agent account. `git log` shows that name on every commit.

`git push -u github main`, remote `https://github.com/linnkhant07/pocket_agent.git`, failed both times with:

```text
fatal: could not read Username for 'https://github.com': No such device or address
```

No GitHub token was created to get around that. `origin` was not force-pushed. It still points at the previous history, so it has diverged from this rewritten `main`.

## 2026-11-03 16:47 PST — a bundle, because the push was blocked

`git bundle create` of `main` at `cb2c0ff` produced a 325 KB file. `git bundle verify` said the bundle is okay and contains a complete history. Paths:

- `/opt/cursor/artifacts/pocket_agent.bundle`
- `/cursor/stores/self/artifacts/pocket_agent.bundle`

Those are the same file. `/opt/cursor/artifacts` is a symlink to the agent store artifacts directory. The frozen snapshot did not push the bundle over 25 MB. It is 325 KB.

## 2026-11-03 evening — the copied sentence, then the copied placeholder

The harness change is in the commits that start at "Stop small models from copying an example answer." The fluent example was deleted. If a request needs a tool and the model answers first, the loop sends one correction naming a tool. Refusals to submit are not corrected, because those tasks are supposed to stop.

Three measurements on the same 50 tasks, `qwen3:0.6b`, native `/api/chat`, thinking off:

| Prompt | Success | Tool accuracy | JSON valid | Restraint | p50 / p95 | llama-server | tokens/s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Example sentence included | 2/50 | 0.40 | 1.0 | 2/18 | 0.449 s / 0.622 s | 1112.5 MB | 45.1 |
| Sentence gone, the word `FIELD` still in the prompt | 1/50 | 0.48 | 0.84 | 0/18 | 0.410 s / 3.040 s | 2057.3 MB | 32.3 |
| No placeholder tokens | 1/50 | 0.40 | 0.41 | 0/18 | 1.347 s / 5.431 s | 3613.7 MB | 31.5 |

The middle run is the interesting one. Tool accuracy went from 0.40 to 0.48, and the trace contained real `profile_get` (3 tasks), `fetch_feed` (2), and `score_fit` (2). The model also wrote `missing_fields: ["FIELD"]`, copying the placeholder that had replaced the example sentence. Taking the placeholders out stopped that copy and also stopped most JSON. Validity fell to 0.41. The one pass was `prefill-auth`. A 0.6B model on this prompt either copies an example or fails to emit JSON. It does not yet follow "call search_jobs."

`qwen3:1.7b` was pulled (1.4 GB) and the VM held it. Same 50 tasks, no placeholder tokens: **1/50**, tool accuracy 0.40, JSON validity 0.558, restraint 1/18, p50/p95 **4.519 s / 11.448 s**, eval RSS 39.0 MB, llama-server **7855.5 MB**, 4,228 completion tokens, **15.6** tokens/s. The pass was `trap-notice`. On the missing-info items it did ask, and the field names were `salary`, `protected_veteran`, and `disability`. The checker wanted `salary_expectation`, `veteran_status`, and `disability_status`, so those turns were marked wrong. One `search_jobs` call happened in the whole suite. The weights fit. The behavior is not a tool-using agent yet.

## Held-out set and the teacher script

`pocket-agent eval --suite heldout` builds 230 tasks: 60 find, 60 screen, 40 track, 40 prefill, 30 traps. Jobs come from the 1,857-row snapshot. Profiles come from `generate_profiles(240, seed=428)`, not from Jordan Hale. Screen labels are whatever `score_fit` returns for that pair. `pytest` runs gold tool traces for one task of each kind; those pass. A 3-task CPU slice on `qwen3:1.7b` scored 0/3. The companies were 66 Degrees, ASML, and AbbVie. JSON validity 0.50, p50 9.192 s, llama-server 4543.6 MB, 14.8 tokens/s. The other 227 were not run on CPU.

`pocket-agent teach` is the Week 2 collector. It refuses to start without `POCKET_AGENT_TEACHER_BASE_URL` and `POCKET_AGENT_TEACHER_MODEL`. A URL on port 11434 uses native Ollama chat, because the OpenAI shim on this build returns empty `content`. Any other URL is `POST {base}/chat/completions` with `POCKET_AGENT_TEACHER_API_KEY`. Traces that fail the checker are dropped. Emails, phone numbers, and the profile name are replaced. The canary stays. A local run, base `http://127.0.0.1:11434/v1`, model `qwen3:1.7b`, smoke suite, limit 2: seen 2, accepted 0, dropped 2. No paid API was called.

`pytest` after these changes: 22 passed.

The GitHub push is still the same error (`could not read Username for 'https://github.com'`). `/opt/cursor/artifacts` is a symlink into the agent store, so the bundle path and the store path are one file.

## 2026-11-03 night — gold traces and a CPU LoRA, no paid teacher

Linn's constraint for this run was free teachers only. The 1.7B Ollama process had already been measured at 7,855.5 MB RSS, so an 8B teacher was not started. The training signal is 68 scripted gold traces, the same shape the harness feeds the model: system prompt, user task, assistant JSON, then `TOOL_RESULT` user turns. They are not the week-1 tasks and not the 230 held-out tasks. Profiles come from `generate_profiles` with seed 7 starting at index 5000. Job ids are `train:*` at fictional companies (Keel Analytics, Brass Lantern, and four others). A test asserts the email set, the prompt strings, and the job ids are disjoint from week 1 and held-out.

Field names: the checker still requires `salary_expectation`, `veteran_status`, `disability_status`, `notice_period`, `willing_to_travel`, `references`, and `demographic`. The ask_user tool description now lists those names, and the gold traces use them. The checker was not given aliases. A pass means the model emitted the name the tool already documented.

TRL 1.17 and PEFT 0.21 trained Qwen3-0.6B on CPU (`use_cpu=True`, fp32, LoRA rank 8 on q/k/v/o, 40 steps, assistant-only loss, max length 768). The run logged `train_runtime` 1008 seconds, `train_loss` 0.2879, and a last `mean_token_accuracy` of 0.9953. `adapter_model.safetensors` is 9,204,512 bytes under `/opt/cursor/artifacts/pocket-qwen3-0.6b-lora`. It is not committed. `scripts/train_mlx_lora.sh` is the Mac path and was not executed. 1.7B was not fine-tuned; the 0.6B train plus four eval passes used the hour.

Eval used a transformers backend, greedy, capped at 96 new tokens, same cap for base and adapter. Process RSS was about 4,742 MB because the weights sit in the eval process. Ollama was stopped before training.

| | Week 1 | Held-out slice (60, including 15 traps) | Tool accuracy | JSON valid | Restraint |
| --- | --- | --- | --- | --- | --- |
| Base, this prompt | 3/50 | 5/60 | 0.40 and 0.367 | 0.767 and 0.742 | 3/18 and 3/26 |
| LoRA | 11/50 | 43/60 | 0.82 and 0.867 | 0.81 and 0.928 | 8/18 and 26/26 |

Base week-1 latency p50/p95 was 2.739 s / 13.243 s, 10.4 tokens/s, 2,717 completion tokens. Base held-out was 6.415 s / 17.308 s, 11.7 tokens/s, 5,903 tokens. Tuned week-1 was 4.907 s / 14.053 s, 10.4 tokens/s, 3,095 tokens. Tuned held-out was 7.234 s / 16.070 s, 9.8 tokens/s, 4,575 tokens. The held-out slice's 15 trap tasks all passed after LoRA. Restraint on that slice was 26/26. The earlier Ollama week-1 number, before the prompt listed the canonical fields, remains 1/50.

The week-1 gain is real and smaller than the held-out gain. Week 1 asks for specific phrases about Contoso, Northwind, and Austin. The gold traces never saw those strings. The held-out companies were also unseen, and the format transferred anyway.

## 2026-11-04 — Groq traces, then a second LoRA

`main` was fast-forwarded from `abb12a6` to `2794c45` (seven commits, no rewrite). That history adds 309 teach tasks on 60 fictional employers and 25 cities, a resumable Groq collector, and `data/teacher/teach_attempts.jsonl` plus 40 accepted traces.

The attempts file has 157 rows: 40 accepted, 117 rejected. By model, `openai/gpt-oss-120b` is 33 accepted out of 75 attempts, and `openai/gpt-oss-20b` is 7 out of 82. The accepted set is 26 find, 12 prefill, 2 screen, and 0 track. The 120b run log stops with Groq's daily token cap: limit 200,000, used 198,536, requested 2,780. The 20b run stops on a time box after 79 attempts in that log line (7 accepted, 721.6 s). gpt-oss was the teacher because Gemini and OpenAI terms forbid training a competing model. gpt-oss weights are Apache-2.0, and the Groq calls were on the free tier. No paid API was used for the student.

The new mix, after dedupe, is 396 chats: 47 from the original gold set (68 before 21 duplicate conversations were dropped), 309 scripted teach-gold traces, and all 40 Groq traces. Teach-gold by skill: find 92, screen 80, track 75, prefill 62. Gold before dedupe: find 19, screen 15, track 14, prefill 20. Invalid Groq turns, the ones that failed JSON schema, were removed before training so the student would not copy `{"action":"search_jobs"}` without a `tool` field. TRL's assistant-only loss then dropped 6 fully masked rows and trained on 390. Sixty-four steps took 2,260 seconds, logged train loss 0.2783, epoch 0.656. Adapter: `/opt/cursor/artifacts/pocket-qwen3-0.6b-lora-mix`, 9,204,512 bytes, not committed. A gold-only retrain, to isolate the 40 Groq traces, did not fit after this run.

Same eval as run 3: transformers, greedy, 96 new tokens, week 1 and the same 60-task held-out slice (15 traps).

| | Week 1 | Held-out 60 | Tool accuracy | JSON valid | Restraint |
| --- | --- | --- | --- | --- | --- |
| Base | 3/50 | 5/60 | 0.40 and 0.367 | 0.767 and 0.742 | 3/18 and 3/26 |
| Gold-only LoRA | 11/50 | 43/60 | 0.82 and 0.867 | 0.81 and 0.928 | 8/18 and 26/26 |
| Mix LoRA | 9/50 | 36/60 | 0.84 and 0.833 | 0.86 and 0.948 | 7/18 and 24/26 |

Mix week-1 latency p50/p95 was 5.584 s / 11.982 s, 10.4 tokens/s, 3,178 completion tokens, process RSS 4,742.0 MB. Mix held-out was 7.548 s / 19.540 s, 9.4 tokens/s, 4,436 tokens, RSS 4,742.3 MB. Thirteen of 15 traps passed. The mix beats the base and loses to the 40-step gold-only adapter. It saw each example less often (0.66 epoch versus 2.35), and the Groq slice has no accepted track traces. That is a description of the run, not a controlled ablation.

## Longer mix run — did 0.66 epoch explain the gap?

The hypothesis was that the mix lost to the gold-only adapter because 64 steps is only epoch 0.656 on 390 rows (97.5 steps per epoch), while the gold-only run reached epoch 2.35. The same 390-example mix was trained again. At step 6 the meter read about 27.7 s/step and still had about 1 hour 25 minutes left on a 190-step job, which is about 88 minutes total, past the 85 minute cutoff. The job was restarted at 175 steps, with adapter checkpoints at 58, 116, and 174. That target is epoch 1.79, not 2.0.

The wall clock did not match the per-step number. `train_runtime` was 9,016 seconds and the meter finished at 2:30:16, an average of 51.5 s/step, while late steps were still about 26 s. Logged train loss was 0.1275. The lowest logged window was 0.003733 at epoch 1.687. There is no checkpoint on that step. The final adapter is `/opt/cursor/artifacts/pocket-qwen3-0.6b-lora-long`, 9,204,512 bytes. The step-58, 116, and 174 checkpoints were not scored. Eval was the final adapter only, same settings as before: transformers, greedy, 96 new tokens, week 1 and the same 60-task slice with 15 traps.

| | Week 1 | Held-out 60 | Tool accuracy | JSON valid | Restraint |
| --- | --- | --- | --- | --- | --- |
| Base | 3/50 | 5/60 | 0.40 and 0.367 | 0.767 and 0.742 | 3/18 and 3/26 |
| Gold-only LoRA, epoch 2.35 | 11/50 | 43/60 | 0.82 and 0.867 | 0.81 and 0.928 | 8/18 and 26/26 |
| Mix, epoch 0.66 | 9/50 | 36/60 | 0.84 and 0.833 | 0.86 and 0.948 | 7/18 and 24/26 |
| Mix, epoch 1.79 | 16/50 | 37/60 | 0.86 and 0.883 | 0.94 and 0.961 | 10/18 and 18/26 |

Epoch 1.79 week-1 latency p50/p95 was 5.322 s / 19.246 s, 10.7 tokens/s, 3,722 completion tokens, process RSS 4,741.7 MB. Held-out was 7.645 s / 24.744 s, 8.1 tokens/s, 4,828 tokens, RSS 5,840.4 MB. Eleven of 15 traps passed.

The hypothesis held on week 1 and not on the held-out slice. Week 1 went 9/50 to 16/50, ahead of the gold-only 11/50, with JSON validity 0.94 and restraint 10/18. The held-out slice went 36/60 to 37/60 and is still behind the gold-only 43/60. Restraint on that slice fell from 24/26 to 18/26. More steps on this mix are not a substitute for the shorter gold-only run on held-out companies.

## 2026-11-04 — Teach suite v2 and a second Groq collection

The mix runs did well on employers they had trained on and worse on new ones. Restraint on the held-out traps fell to 18/26 in the longer run. A second, wider teacher set was built to give more variety and more restraint cases.

`src/pocket_agent/teacher_tasks_v2.py` has 318 tasks over 100 new fictional employers (200 jobs, `teach2:*` ids). They come from word lists that are separate from week 1, the held-out set, and teach v1. There are 31 locations, 24 role families times 8 seniority labels, and several phrasings per request type, picked with a fixed seed. 143 of 318 tasks (45%) are restraint tasks: 36 ask_user with the canonical field names, 20 submit refusals, 18 unsafe requests (fake references, invented credentials, recruiter home addresses), 10 out-of-scope requests (flights, payments, signing an offer), 14 clearance exclusions, and the ineligible fit checks. A test checks that no v2 employer word shows up in any eval or held-out prompt or expectation, and that emails, prompts, and job ids do not overlap with held-out, gold, or teach v1. Profiles are synthetic (seed 53, start index 40000, @example.com). Every gold script passes the checker.

The collector got `--suite v2`, a compact teacher-only system prompt (the trace and the trainset keep the harness `SYSTEM_PROMPT`), shuffled task order, and a 700-token completion cap. Any HTTP 400 now counts as an invalid turn. Both free-tier models ran at the same time on 2026-11-04 and both stopped at Groq's 200,000 tokens/day cap after about 30 minutes. One short retry later added one more 120b trace.

| Model | Attempted | Accepted | Prompt tokens (cached) | Completion tokens |
| --- | --- | --- | --- | --- |
| openai/gpt-oss-120b | 97 | 63 | 140,199 (44,800) | 17,058 |
| openai/gpt-oss-20b | 177 | 45 | 74,880 (15,104) | 9,056 |

Groq's cap counter read 199,119 used for 120b and 199,875 for 20b when the runs stopped. The 120b number includes usage left over from the day before. The 108 accepted traces cover 98 distinct tasks. 65 of them (60%) are restraint tasks, because those were accepted more often than search and track tasks. By skill: track 37, prefill 35, find 21, screen 15. Files: `data/teacher/teach_traces_v2.jsonl`, `teach_attempts_v2.jsonl`, `teach_traces_v2.runs.jsonl`. `mix.groq_rows()` now loads v1 and v2 by default (v2 rows are tagged `groq_v2`), so the training mix is gold + teach-gold + Groq v1 + Groq v2. No training was run.

## 2026-11-04 — Pause the fine-tune, build a form filler

The longer mix run is the reason this stopped. Week 1 reached 16/50 and the held-out slice stayed at 37/60, behind the gold-only adapter at 43/60. A 0.6B model asked to do the whole task did not generalize to employers it had not trained on. The product is now a form filler: deterministic page reading and filling, a small SQLite memory, and a constrained local model only when a label is still unresolved.

`pocket-agent fill <url>` uses Playwright. It does not click a submit control. The block is the accessible name (Submit, Apply), `type=submit`, an onclick that submits, and a last-step line such as "Step 2 of 2". Sensitive answers are asked unless the user has saved an explicit always-use value. The suite is 29 fictional forms on localhost (14 dev, 15 held-out, 18 of them Workday-shaped, including a Next button that is `type=submit` and a last Next that submits from a listener). Held-out labels are not copies of the dev labels. The answer key is the fictional Jordan Hale profile.

Three clean runs with no GGUF loaded, then the held-out forms again under four perturbations:

| Run | Dev fill | Held-out fill | Wrong fills | Asks | Submits | Crashes | Max seconds | RSS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| none, runs 1–3 | 108/108 | 111/111 | 0 | 10/10 and 6/6 | 0 | 0 | 1.41 dev, 0.99 held | 646–659 MB |
| slow, modal, conditional, validation | — | 111/111 each | 0 | 6/6 | 0 | 0 | 1.66, 1.06, 0.98, 1.27 | up to 672 MB |
| Qwen3-0.6B Q8, one run | 108/108 | 111/111 | 0 | 10/10 and 6/6 | 0 | 0 | 1.43 dev, 0.95 held | 1561 MB |

Repeat-asks were 0. The 0.6B file is 639,446,688 bytes. It loaded in the process and was not called for any suite field, because the synonym table already matched. 1.7B and 4B were not scored for that reason. One extra prompt, "Preferred contact handle", with email, phone, and GitHub as the only allowed keys, returned `ask` in 2.93 s. This VM is Linux CPU. Chromium was headless. No real job site was opened, and the Mac Metal path was not run. The bar set for the suite (wrong-fill ≤1%, fill accuracy ≥95%, sensitive fields asked or explicitly saved, 0 submits, repeat-ask ≤2%, 0 crashes over 3 runs, under 90 s) is met by the rows above.

## 2026-11-04 — Realistic hard forms

The first form suite was too easy: every label was already a synonym, so the model was never called, and the same person wrote the matcher and the labels. A second suite of 20 forms uses wording and mechanics from real ATS pages (Workday, Taleo, Oracle Cloud HCM, Greenhouse, and custom), without personal data and without real employer sites. Held-out sentences are not the dev sentences and are not entries in the synonym table.

Always asked, unless an explicit saved answer exists: salary, start date, essays and cover letters, spoken languages and interview language, export control, the government and conflict-of-interest chain, "will you have the degree by your start date," graduation-window checks, hybrid or on-site schedule and office choice, EEO, e-signature, certification, and arbitration. Work authorization is not treated as sponsorship. A login wall or a CAPTCHA stops the run. Continue on a Review step is classified as submit and is not clicked. Resume-parse mistakes (a mangled date, a truncated school, a misspelled employer) are overwritten from the profile. Typeahead school and company fields are filled by typing and choosing a suggestion.

Non-exact matches are sent to the model. If it names another key, the keyword already in the label is kept. On a probe, 0.6B alone asked, or picked LinkedIn, for the H-1B sponsorship question. One 4B run, before that guard, wrote the applicant's name into an empty referrer field. After the guard, empty referrer asks, and a later form reuses the saved name.

| Suite | Model | Dev | Held-out | Wrong | Calls on held-out | Seconds per call | Max form | RSS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Easy | none | 108/108 | 111/111 | 0 | 0 | — | 1.57 s | 646 MB |
| Easy | 0.6B Q8 | 108/108 | 111/111 | 0 | 33 | 1.9 | 12.2 s | 1670 MB |
| Easy | 1.7B Q8 | 108/108 | 111/111 | 0 | 33 | 4.9 | 28.6 s | 2816 MB |
| Easy | 4B Q4 | 108/108 | 111/111 | 0 | 33 | 5.7 | 29.7 s | 5192 MB |
| Hard | none, 3 runs | 31/31 | 22/22 | 0 | 0 | — | 0.87 s | 656 MB |
| Hard | 0.6B Q8, 3 runs | 31/31 | 22/22 | 0 | 20 | 1.9 | 14.8 s | 1679 MB |
| Hard | 1.7B Q8 | 31/31 | 22/22 | 0 | 20 | 4.8 | 33.6 s | 2812 MB |
| Hard | 4B Q4 | 31/31 | 22/22 | 0 | 20 | 6.5 | 50.3 s | 5156 MB |

Asks were complete on every row, submits were 0, repeat-asks were 0, and crashes were 0. The hard held-out bar is met by `none` and by all three GGUF sizes. 0.6B is the smallest GGUF that meets it. `none` also meets it. Real pages and a Mac were not tested.

## 2026-11-04 — Local chat desk

`main` stayed at `9e410a3`. The desk is on branch `ui`. `pocket-agent ui` listens on `127.0.0.1:8765` only and opens a browser. The page, styles, and script are one file served by FastAPI. Nothing is loaded from a CDN.

Start pastes an application link. The existing `fill_form` runs in a second Chromium window, headed unless the background toggle is on. Each field is streamed to an activity list with its step, value, and source (resume, memory, model, or answer). Questions are chat bubbles: text, a dropdown, yes/no buttons, a date, or a larger box for an essay. "Remember this" starts checked, and starts unchecked for sensitive answers. Review shows the filled, asked, and skipped counts and tells the user to click Submit in the form window. A login wall or a CAPTCHA pauses and waits for Continue. My answers edits the profile, replaces the resume, and edits or deletes memory rows. Settings lists the GGUF files and the model size. `pocket-agent fill` is unchanged.
