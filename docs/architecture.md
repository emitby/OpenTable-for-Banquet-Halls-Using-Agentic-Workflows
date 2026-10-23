# Architecture

## Layers

| Layer | File | Knows about |
|---|---|---|
| Host safety | [`banquet/paths.py`](../banquet/paths.py) | Path confinement and atomic writes (no `os`, no deletions) |
| Storage | [`banquet/db.py`](../banquet/db.py), [`banquet/states.py`](../banquet/states.py) | SQLite schema; seed data for every state and DC; state names, codes and tax rates |
| Domain | [`banquet/services.py`](../banquet/services.py) | Search (including by state), availability, pricing, holds. **No AI.** |
| Guardrails | [`banquet/guardrails.py`](../banquet/guardrails.py) | G1–G7 as plain functions ([guardrails.md](guardrails.md)) |
| Agent runtime | [`banquet/agents/runtime.py`](../banquet/agents/runtime.py) | The Claude tool-use loop, schema checks (G2), budgets (G6), events. **No booking logic.** |
| Agent team | [`banquet/agents/team.py`](../banquet/agents/team.py) | Prompts, tool wiring, delegation, session state, G1/G3/G4 |
| Harness | [`banquet/harness/`](../banquet/harness) | Traces, the token ledger, scenarios, scorers, the simulated client ([harness.md](harness.md)) |
| Interfaces | [`banquet/api.py`](../banquet/api.py), [`banquet/serve.py`](../banquet/serve.py), [`banquet/cli.py`](../banquet/cli.py), [`static/index.html`](../banquet/static/index.html) | HTTP (127.0.0.1), terminal, browser |

The domain layer is plain, deterministic Python. Both the agents (through tools) and the API (for human-only actions) use it, and it has its own tests. The model chooses *which* operations to run, and the code decides *what they return*.

## A customer turn

```mermaid
sequenceDiagram
    actor U as Customer
    participant C as Concierge
    participant S as Venue Scout
    participant P as Pricing Analyst
    participant D as BanquetService

    U->>C: "Wedding for 180 in Austin, Texas next October, ~$45k"
    Note over C: G1 input check first
    C->>S: ask_venue_scout(brief)
    Note over S: fresh context
    S->>D: search_venues(state="TX", city="Austin", guest_count=180, ...)
    S->>D: get_venue_details / check_availability (×N)
    S-->>C: shortlist with venue, hall and package ids
    C->>D: show_venues([ids])
    C->>P: ask_pricing_analyst(exact options + budget)
    Note over P: fresh context
    P->>D: build_quote (×N)
    P-->>C: totals, per-guest, deposits, savings ideas
    Note over C: G4 checks every price in the reply
    C-->>U: recommendation and venue cards
    U->>C: "Hold the first option. Maria Lopez, maria@example.com"
    C->>D: place_hold(...) after G3 checks
    C-->>U: hold placed, expires in 72h
    U->>D: POST /api/bookings/{id}/confirm   (human only)
```

## The agent loop

`Agent.run(messages)` in [`runtime.py`](../banquet/agents/runtime.py):

1. Check the token budget (G6), then call `client.beta.messages.create` with the agent's system prompt, tools, `output_config.effort`, prompt caching and server-side fallbacks. Charge the reported usage to the budget, and emit a `model_call` event.
2. Append the assistant's full `content` to the history, including thinking and tool-use blocks. The history is never edited.
3. On `refusal`, return a safe reply. If there are no tool calls, return the text.
4. Otherwise run every `tool_use` block, concurrently when there are several, and append **all** results in **one** user message.
   - Arguments are checked against the tool's JSON schema (G2) and the handler's signature before it runs.
   - `BookingError` and invalid arguments become `is_error: true` results the model can act on.
   - Unexpected exceptions are logged and returned as a generic error.
5. Repeat, up to `max_turns`, then raise `AgentError`.

## Delegation

A specialist is a `Tool` whose handler runs another `Agent` on a single-message conversation:

```python
Tool("ask_venue_scout", ..., lambda brief: self._delegate(self.scout, brief))
```

Benefits:

- **Context isolation.** The Scout may read a dozen venue records. The Concierge receives only the shortlist.
- **Cost control.** Specialists run at `low` effort because their tasks are narrow.
- **Parallelism.** Two delegations in one Concierge turn run at the same time.
- **Traceability.** Every agent emits `tool_call` and `tool_result` events into the turn's trace, which the UI shows as *Agent activity*.

## Safety boundaries

| Action | Who can do it |
|---|---|
| Search venues, read venue details | Venue Scout (search, details); Pricing Analyst (details) |
| Check availability | Venue Scout, Concierge |
| Build quotes | Pricing Analyst |
| Place a 72-hour hold | Concierge only, after the customer asks and gives a name and email. The email must be one the customer typed, with at most 3 active holds (G3) |
| Confirm or cancel a booking | The customer only, through the API or UI. No agent has these tools |

Holds are non-binding and expire on their own. An expired hold no longer blocks the date and can't be confirmed.

## Pricing rules

Defined as constants at the top of [`services.py`](../banquet/services.py):

| Rule | Value |
|---|---|
| Friday/Saturday rental premium | +25% |
| Peak months (May, Jun, Sep, Oct, Dec) rental premium | +15% |
| Food & beverage | per-guest price × max(guests, hall minimum) |
| Service charge | 20% of food & beverage |
| Sales tax | The venue's rate × subtotal. Approximate state base rates from [`states.py`](../banquet/states.py); the original venues use combined local rates (NYC 8.875%, Glen Cove 8.625%, Jersey City 6.625%) |
| Deposit on confirmation | 25% of total |
| Hold duration | 72 hours |
| Booking window | up to 2 years ahead |
