# Lessons

Carried over from earlier projects (same user, same CLAUDE.md):

- **Never write outside the project folder (user correction, 2026-10-21).**
  - Temporary files go in `.cache/` only.
  - No WebFetch of PDF or other binary files.
  - Agent prompts must say "save nothing anywhere".
  - Run agents and long commands in the foreground.
  - No memory files under `~/.claude`.
  - Check every write path.
- **Never use `os`.** Use pathlib and `confine()`. Keep the bytecode cache, the pytest cache and test scratch files inside the project. pytest's `tmp_path` writes to the system temp folder, so use in-memory SQLite or a project-local folder instead. pip needs `--no-cache-dir`.
- **Docstrings on every module, class and function** (args, returns, raises, side effects). Comments explain rules and decisions.
- **Scripted text replacements must assert that their target exists.**
- **Re-derive everything downstream of a change.**
- **Screenshots must prove the requirement visibly.**
- **Test every guardrail in both directions:** it blocks the bad case and allows the benign look-alike.
- **Don't fix an unsupported claim with another unsupported claim.**
- **Ids aren't unique once runs are combined** (2026-10-22). Scripted clients each numbered tool calls from `sim_1`, so the suite ledger credited a delegation's tokens to `build_quote`. Key ids by their scope (scenario), and give generated ids a random prefix.
- **Evidence for a check must be the same kind of thing as the claim.** G4 first accepted any number in a tool result, so a guest count of 180 "grounded" an invented "$180 per guest". Compare money to money, and test with the look-alike numbers.
- **Match whole tokens, not substrings.** The email check accepted `lopez@example.com` because it's inside `maria.lopez@example.com`.
- **Parallel tool calls break check-then-act rules.** Two holds sent together both passed the "fewer than 3" check. Lock the check and the write together.
- **Third-party tools write to the system temp folder.** Chromium's throwaway profile went there until the launchers pointed TEMP/TMP/TMPDIR at `.cache/tmp`.
- **Simulated numbers must behave like real ones.** Simulated reasoning was counted as output but never kept in the history, which made result tokens look tiny. Mirror the real API's behavior, and label simulations everywhere.
- **Test on the oldest Python that CI runs, not just the local one** (2026-10-23). The guide used backslashes inside f-string expressions, which Python 3.12 allows and 3.10/3.11 reject, so CI failed after the push. There's now a tokenizer-based test that catches it on 3.12, and I watch CI after every push.
- **Check what I say against the files.** I said this project had no CLAUDE.md when it did (2026-10-22). Re-check before stating that something is absent.
