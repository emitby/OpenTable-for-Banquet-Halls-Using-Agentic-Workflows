"""Evaluation suite for Banquet Concierge (see ``docs/evals.md``).

Five layers, from cheapest to most realistic:

1. Unit tests (``tests/``): booking rules, guardrails, agents, harness, API.
2. Red-team (:mod:`evals.redteam`): attacks on every guardrail, each paired with a
   benign look-alike that must still work.
3. Independent pricing cross-check (:mod:`evals.crosscheck_pricing`): quotes re-derived
   from the raw database by code that shares nothing with ``banquet.services``.
4. Scenario scorers (:mod:`evals.scorers`): the harness's eight scenario scorers plus
   six eval scorers, run on every scripted conversation.
5. Live trials (:mod:`evals.run_evals` ``--live --trials N``): the real agents, scored
   the same way, with the pass rate per scorer across trials.
"""
