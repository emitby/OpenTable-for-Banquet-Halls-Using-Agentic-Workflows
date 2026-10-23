# Guardrails

Prompts describe the rules so the agents usually comply on the first try, but **every guardrail is enforced in code** ([`banquet/guardrails.py`](../banquet/guardrails.py)). Nothing depends on a model obeying.

Each guardrail is tested in both directions: the bad case is blocked, and the harmless look-alike still works.

| # | Harm | Guardrail | Enforced in | Tests |
|---|---|---|---|---|
| G1 | Prompt injection, junk input | Removes control characters and limits length to 4,000 characters. Refuses phrasing aimed at the model's own instructions: an override verb followed directly by *your* / *previous* / *system* instructions or "the rules above", plus "system prompt", "you are now", "developer mode", "act as the admin" and "your new instructions". Refused messages never reach a model, so they cost nothing. | `ConciergeSession.send` | `test_guardrails.py` (7 blocked, 6 benign, including "ignore the previous date", "new instructions for the caterer" and "don't forget the house rules"), `test_agents.py`, `test_api.py` |
| G2 | Malformed tool calls reaching booking code | Every tool call is validated against the tool's JSON schema: type, `minimum`/`maximum`, `maxLength`, `minItems`/`maxItems`, `required`, and no extra fields. Problems go back as `is_error` results that name the field and the fix. | `Agent._execute` | `test_guardrails.py` (9 violation kinds), `test_agents.py` |
| G3 | Unwanted or fabricated holds | At most **3 active holds** per conversation. The `customer_email` must be exactly (ignoring case) an address the customer typed, not part of one, so an agent can't invent one or reuse another customer's. Holds requested in parallel are checked one at a time under a lock. Refusals are recorded as guardrail events. | `ConciergeSession._place_hold` | `test_agents.py` (invented email, hold limit, parallel holds), `test_guardrails.py` (partial email), scenario `hold-without-email` |
| G4 | Invented prices | Every dollar amount in the Concierge's reply must match a **dollar amount** from this session's tool results (the money fields of JSON results, such as `total` or `per_guest_price`, and `$`-figures in specialists' replies) or the customer's own messages, **at the precision written**. Guest counts, ids and percentages never count as evidence. "$58k" may stand for $58,412.50; "$58,400" may not. Otherwise the reply goes back once with a correction note. If the corrected reply still has an unsupported amount, a warning note is added. | `ConciergeSession.send` | `test_guardrails.py` (parsing, rounding, invented amounts, guest counts and percentages not counted), `test_agents.py` (correction, fallback), scenario `denver-price-guess` |
| G5 | Personal data in logs | Emails are replaced with `<email>` in traces and run reports. | `harness/trace.py` | `test_guardrails.py`, `test_harness.py` |
| G6 | Runaway cost | A token budget of 400,000 per customer turn and 2,000,000 per conversation, shared by all three agents. It's checked before every model call, on top of each agent's turn limit and the 500-session cap. The counters are thread-safe; because the two specialists can run in parallel, a turn can overshoot by the calls already in flight. The API answers 429 with a friendly message. | `Agent.run`, `TokenBudget` | `test_guardrails.py`, `test_agents.py` (no further call after the budget) |
| G7 | An agent committing the customer | No agent has a confirm or cancel tool. Only the customer can confirm, through the API, the web button or the CLI (after a y/N prompt), and the email must match. | `team.py`, `api.py` | `test_agents.py`, `test_api.py` |

## Host safety (CLAUDE.md)

- **No `os` module, no file deletions.** Source scans in `tests/test_project_guardrails.py` check every Python file.
- **Paths are confined to the project.** Every path goes through [`paths.confine`](../banquet/paths.py), which also handles `..` escapes and the Windows `\\?\` prefix.
- **Writes are atomic.** Each file is written as `.tmp`, then renamed into place.
- **The database is never migrated in place or deleted.** The new default file is `data/banquet-v2.db`.
- **Temporary files stay inside the project.** The launchers point TEMP/TMP (and TMPDIR) at `.cache/tmp`, so tools such as Chromium create their throwaway files there.
- **Tests stay inside the project.** They use in-memory databases and `.cache/test-output/`, never the system temp folder. The pytest cache is disabled.
- **The server binds to 127.0.0.1 only.** Every response carries security headers: CSP, `nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`.

## Known limits

- **G1 catches known phrasings.** A novel injection can pass the code check; the agents' instructions are the second line of defense.
- **G4 only checks the Concierge's reply.** The specialists' replies count as evidence, so a specialist's own arithmetic, such as "saves $4,312", is trusted.
- **G4 checks dollar amounts, not every fact.** Venue names and amenities in the reply aren't verified by code.
- **Confirmation is protected by an email match, not authentication.** This is a prototype with no accounts.
