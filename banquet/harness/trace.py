"""Write a run's events to ``trace.jsonl`` and JSON reports, with privacy redaction.

Every line of ``trace.jsonl`` is one event (a model call, tool call, tool result or
guardrail event) plus the scenario and turn it belongs to. Emails are replaced with
``<email>`` (guardrail G5) before anything is written. All paths go through
:func:`banquet.paths.confine`, and files are written atomically; nothing is deleted.
"""

import json
from datetime import datetime

from banquet.guardrails import redact
from banquet.paths import RUNS_DIR, confine, write_text


def new_run_dir(label):
    """Create a fresh, uniquely named run folder ``runs/<timestamp>-<label>/``.

    Args:
        label: A short name, e.g. ``"simulated"``.

    Returns:
        The folder's path.

    Raises:
        FileExistsError: Never in practice; ``mkdir(exist_ok=False)`` guarantees a run
            never writes into an earlier run's folder.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = confine(RUNS_DIR / f"{stamp}-{label}")
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_trace(path, events):
    """Write events as JSON Lines (one redacted event per line).

    Args:
        path: Target file inside the project.
        events: Event dicts.

    Returns:
        The path written.
    """
    lines = [json.dumps(redact(e), default=str) for e in events]
    return write_text(path, "\n".join(lines) + "\n")


def write_json(path, data):
    """Write a redacted, pretty-printed JSON report. Returns the path written."""
    return write_text(path, json.dumps(redact(data), indent=2, default=str) + "\n")
