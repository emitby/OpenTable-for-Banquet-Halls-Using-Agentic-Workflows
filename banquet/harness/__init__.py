"""The harness: tracing, token accounting and scripted scenarios around the agent team.

* :mod:`banquet.harness.trace`: writes every event of a run to ``trace.jsonl``
  (emails redacted).
* :mod:`banquet.harness.tokens`: turns ``model_call``/``tool_result`` events into a
  token and cost ledger per agent and per tool.
* :mod:`banquet.harness.scenarios`: scripted customer conversations and the code
  checks (scorers) each must pass.
* :mod:`banquet.harness.simulated`: :class:`SimulatedClient`, a scripted stand-in for
  Claude that drives the real tools and estimates token usage. Used for offline tests,
  screenshots and the PDF guide; its numbers are labeled "simulated" everywhere.
* :mod:`banquet.harness.run_scenarios`: runs the scenarios (simulated by default, or
  live with a cost cap) and writes ``runs/<id>/``.
"""
