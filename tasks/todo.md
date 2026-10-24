# Task 1: Guardrails, harness, venues in every state, PDF guide

User request (2026-10-22): "take the project and add guardrails and a harness that are well documented and create a pdf of what it does and shows the tokens used for each tool and a chart of the agentic workflows, and also maps out the functionality of the program. also make it so that it can search for venues in any state. make sure the python code has docstrings and comments where appropriate."

User decisions:
- **Venues:** fictional venues in all 50 states plus DC. No outside venue API.
- **No live API calls.** Token numbers in the PDF come from a **simulated** run and are labeled as simulated. The live mode is built but not run.
- No GitHub push unless asked.
- Everything stays inside this folder: caches and the browser in `.cache/`, no deletions, no `os`.

## Plan

- [x] **0. Housekeeping**
  - `tasks/lessons.md`
  - `run.ps1` / `run.sh` launchers that keep the Python cache and the Playwright browser in `.cache/`
  - `.gitignore`: `.cache/`, `runs/`
- [x] **1. CLAUDE.md compliance**
  - Remove `import os` from `api.py`: the `BANQUET_DB` environment variable becomes a `--db` option on the launcher.
  - Add a `paths.py` with `confine()` for every file path.
  - README: replace "delete the file to reset it" with "point `--db` at a new file".
  - Add docstrings and reasoning comments to every module, class and function.
  - Tests that scan for `os`, deletion calls and missing docstrings.
- [x] **2. Venues in every state**
  - Add a `state` column to venues.
  - Keep the 10 NY/NJ venues, and add about 2 fictional venues for every other state plus DC. A deterministic generator builds them from real city names, with made-up venue names and addresses marked "(fictional)".
  - `search_venues(state=â€¦)` accepts "TX" or "Texas".
  - Sales tax per state: the state base rate, labeled approximate. NYC venues keep 8.875%.
  - Update prompts, the UI greeting and the suggested prompts so they're nationwide.
  - A new default database file, so the old one is neither migrated in place nor deleted.
- [x] **3. Guardrails** (`banquet/guardrails.py`, each enforced in code and tested)
  1. **Customer input:** length limit, control characters stripped, prompt-injection phrasing declined politely.
  2. **Tool arguments:** validated against each tool's JSON schema (types, ranges, required fields, no extra fields) before the handler runs.
  3. **Holds:**
     - at most 3 active holds per session
     - `place_hold` refused unless the customer's email appears in the customer's own messages, so an agent can't invent one
     - only the customer can confirm or cancel (existing rule, now tested)
  4. **Price grounding:** every dollar amount in the Concierge's reply must appear in a tool result from this session. Otherwise the reply is sent back once for correction, with a safe fallback after that.
  5. **Privacy:** emails are redacted in traces, logs and reports.
  6. **Budgets:** a token budget per turn and per session (checked before every model call), plus the existing turn limits and session cap.
  7. **Host safety (CLAUDE.md):** no `os`, confined paths, no deletions, the server bound to 127.0.0.1, security headers.
- [x] **4. Harness** (`banquet/harness/`)
  - **Trace recorder:** every model call (agent, usage, latency, stop reason), every tool call (input with emails redacted, ok or error, latency, result size) and every guardrail event.
  - **Token ledger by agent and by tool:**
    - Tokens to call a tool = the output tokens of the call that requested it, split by size when several tools are called at once.
    - Tokens for a tool's result = the input-token increase on the agent's next call, split the same way.
    - Delegation tools (`ask_venue_scout`, `ask_pricing_analyst`) also carry the specialist's whole usage.
    - Cost uses claude-opus-5-5 prices.
  - **Scripted customer scenarios** (for example a wedding in Texas, a corporate event in Ohio, a NYC gala, an off-topic request, an injection attempt), scored by code checks.
  - **SimulatedClient:** plays the agents' tool calls against the real booking service and estimates tokens from text size. Its output is labeled simulated.
  - A **live mode** with a cost cap. It's built but not run.
  - **Run folder** `runs/<id>/`: `trace.jsonl`, `manifest.json`, `tokens.json`.
- [x] **5. PDF guide** (`docs/how-it-works.pdf`): HTML rendered to PDF by Chromium through Playwright. Sections:
  - what the app does
  - a functionality map (SVG)
  - an agentic workflow chart (SVG)
  - guardrails
  - the harness
  - a token-per-tool table and bar chart, plus a per-agent breakdown, labeled simulated
  - screenshots of the web UI running on the simulated client
- [x] **6. Tests** for state search and tax, guardrails (both directions), the token math, simulated scenarios, the API and the docstring scan. Keep the existing 20 tests passing.
- [x] **7. Docs:** README, `docs/architecture.md`, `docs/guardrails.md`, `docs/harness.md`.
- [x] **8. Verify:** tests pass, the PDF is rendered and reviewed visually by an independent subagent, findings are fixed, and a review section is added here.

## Review (Task 1)

- **Built:**
  - venues in all 50 states plus DC: 214 fictional venues, a `state` filter, per-state tax
  - guardrails G1â€“G7 in `banquet/guardrails.py`
  - the harness: events, redacted traces, a token ledger per agent and per tool, 6 scenarios, 8 scorers, simulated and live modes
  - `docs/how-it-works.pdf` (11 pages): screens, a functionality map, an agentic workflow chart, guardrails, the harness, and tokens per tool and per agent, labeled simulated
  - launchers; no `os`; path confinement; docstrings everywhere
- **Verified:**
  - 205 tests pass (179 at first; 26 more after the CI fix)
  - the simulated suite passes 6/6 scenarios with 8/8 scorers each
  - I reviewed every PDF page visually (rendered to PNG in `.cache/`)
- **Found and fixed during the build:**
  - "Here are 1 options"
  - one venue per city
  - a booked date not being mentioned
  - an injection false positive ("new instructions for the caterer")
  - id collisions in the suite ledger, which gave `build_quote` specialist tokens; now has a regression test
  - simulated reasoning missing from the history
  - PDF layout: an overflowing card, an overlapping label, orphaned rows
- **Independent review: 17 findings, all fixed.**
  - G4 accepted non-money numbers as evidence.
  - The G3 hold limit could be raced by parallel calls.
  - The G3 email check matched substrings.
  - Chromium's temp profile went to the system temp folder; the launchers now use `.cache/tmp`.
  - Live mode would have failed by design on G3/G4 expectations.
  - Some PDF tables weren't labeled simulated, `{{id}}` printed literally, "51 states + DC" was wrong, and "1 steps" was ungrammatical.
  - G1 false positives on "forget the house rules".
  - Doc and table inaccuracies, cost-cap wording, a budget-counter lock, more state-name forms, and caveats about the token method.
- **Not done (by user decision):** no live API run. The token numbers are simulated estimates.
- **Not pushed to GitHub** (not requested).
- **Outside-folder note:** before the fix, the first three PDF builds let Chromium create its throwaway profile in the system temp folder. Chromium removes that profile itself when it closes; I didn't access or delete anything there.

# Task: comprehensive evals (2026-10-24)

Request: "add comprehensive evals and then update the pdf's and commit all changes to github and retag once they have evals".

- [x] Red-team (`evals/redteam.py`): 66 cases (46 attacks, 20 benign look-alikes) across G1–G7 and the booking rules
- [x] Independent pricing cross-check (`evals/crosscheck_pricing.py`): 4,816 quotes, 302 halls, 50 states + DC, 0 mismatches
- [x] Six eval scorers (`evals/scorers.py`) on top of the eight harness scorers; three new scenarios (DC, Wyoming by name, oversized message)
- [x] Runner (`evals/run_evals.py`, `run.ps1 eval`): offline layers plus live trials with pass rates per scorer; results in `evals/results/`
- [x] Planted-defect tests (`tests/test_evals.py`): each layer fails when its guardrail or rule is broken; 255 tests pass
- [x] PDF guide: new Evals section, cover stats, nine scenarios; README results-first layout; `docs/evals.md`
- [ ] Live trials: blocked. The first live trial stopped when the Anthropic account ran out of API credits (6 of 9 conversations ran). The runner now records such errors and stops cleanly instead of crashing.

## Review
- Found while building: the hold-limit red-team first read the limit from the code, so raising it passed vacuously; it now checks the documented policy of 3.
- Found while building: a live API error crashed the runner and lost the scored conversations; errors are now recorded per conversation and account problems stop the run with the reason.
- Fixed in the README: double-encoded characters (`â†’`, `Â·`, box-drawing) that GitHub showed as garbage.
