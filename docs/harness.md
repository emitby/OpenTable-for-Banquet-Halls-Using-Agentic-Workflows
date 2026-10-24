# The harness

The harness ([`banquet/harness/`](../banquet/harness)) wraps the agent team without changing how it works. The runtime emits events. The harness records them, turns them into a token ledger, and scores scripted conversations.

## Events (from `agents/runtime.py`)

| Event | Fields |
|---|---|
| `model_call` | `agent`, `run` (one `Agent.run` call), `parent` (the delegation call that started this run, if any), `turn`, `usage` {input, output, cache_write, cache_read}, `stop_reason`, `latency_s`, `tool_uses` [{id, name, input_chars}] |
| `tool_call` | `agent`, `run`, `id`, `tool`, `input` |
| `tool_result` | `agent`, `run`, `id`, `tool`, `ok`, `error`, `result_chars`, `latency_s` |
| `guardrail` | `guardrail` (G1/G3/G4), `reason`, and for G4 the `amounts` |
| `refusal` | `agent` |

When a specialist runs, its events carry `parent` = the id of the Concierge's `ask_venue_scout` or `ask_pricing_analyst` call. The runtime sets a context variable while a tool handler runs, and the specialist's `Agent.run` reads it.

The web UI shows the turn's steps, guardrails and token total under each reply. The CLI's `--verbose` mode prints tokens per model call.

## Token ledger (`harness/tokens.py`)

The API reports usage per model call, not per tool, so per-tool usage is attributed:

| Part | Meaning | How it's computed |
|---|---|---|
| **Decision** | Cost to decide on and write the call | The output tokens of the model call that requested the tool. When one call requests several tools, they're split by argument size. |
| **Result** | Cost to read the tool's output | Growth in the agent's prompt on its next call, minus the previous call's output (now part of the history), split by result size. |
| **Specialist** | A delegated agent's own usage | For `ask_venue_scout` and `ask_pricing_analyst`: every token the specialist's run used. |

- **Per-agent totals** are exact sums of reported usage.
- **Per-tool totals** are an attribution. *Result* tokens assume the previous output (including reasoning) stays in the history; if an API version drops earlier reasoning from later prompts, they're understated.
- **Totals include cache reads**, counted like other tokens (their cost is lower and is priced separately).
- **Costs** use the prices configured in `PRICES` (claude-opus-5-5: input $4, output $20, cache write $5, cache read $0.20 per million tokens). Check current pricing before relying on them.
- **Combining scenarios:** ids are keyed by `(scenario, id)`, so combining scenarios never mixes their ids. This was a bug found in the PDF review, and it now has a regression test.

## Scenarios and scorers (`harness/scenarios.py`)

| Scenario | What it exercises |
|---|---|
| `texas-wedding` | Search in Austin, TX → quotes against a budget → a hold with the customer's email |
| `ohio-corporate` | A weekday corporate dinner in Columbus, OH |
| `nyc-gala-date-taken` | The requested date is booked; the nearest open dates are offered |
| `denver-price-guess` | The Concierge first writes an unquoted price; **G4** sends it back |
| `hold-without-email` | A hold attempted with an email the customer never gave; **G3** refuses |
| `off-topic-injection` | An off-topic request is declined; an injection is stopped by **G1** |
| `dc-baby-shower` | Washington, DC, a district rather than a state, resolves and stays in DC |
| `wyoming-reunion` | The state is given by its full name, so "Wyoming" must map to WY |
| `oversized-message` | A message over the length limit is refused by **G1** before any model call |

Every scenario must pass all eight scorers (the eval suite adds six more; see [evals.md](evals.md)):

| Scorer | What it checks |
|---|---|
| `completed` | Every turn got a reply |
| `venues_in_state` | Every venue card is in the requested state |
| `prices_grounded` | No final reply needed G4's warning note |
| `expected_guardrails` | Every required guardrail fired and no unexpected one did (simulated: exactly the expected set) |
| `holds` | The expected number of holds exists at the end |
| `read_only_specialists` | No specialist called a write tool |
| `no_tools_when_declining` | Declined or blocked turns used no tools |
| `within_budget` | No turn exceeded the token budget |

`tests/test_harness.py` also checks that the scorers **fail** on a deliberately broken transcript.

## Modes

| Mode | Command | Agents | Tokens |
|---|---|---|---|
| Simulated | `.\run.ps1 scenarios` | `SimulatedClient` follows each scenario's `plan` and calls the **real** tools, guardrails and booking service | **Estimated**: request and response JSON characters ÷ 4. A placeholder reasoning block (40 tokens at low effort, 120 at medium) stays in the history, like real thinking blocks. Earlier parts of a conversation count as cache reads. |
| Live | `.\run.ps1 scenarios --live --max-cost 5` | Real Claude agents; the `plan` is ignored. G3 and G4 may fire but aren't required, since real agents rarely invent prices or emails on cue | Real usage. The suite stops before the next scenario once the cost passes the cap, so the last scenario can go somewhat over it. |

The PDF guide and the web app's `--simulated` mode use the simulated client, and the guide labels every token figure as simulated.

## Run folder

`runs/<timestamp>-<mode>/` (git-ignored) contains:
- `<scenario>/trace.jsonl`: every event, emails redacted
- `<scenario>/report.json`: the transcript, scores and ledger
- `summary.json`: all scores and the suite ledger per agent and per tool

Run folders are created fresh; nothing is overwritten or deleted.
