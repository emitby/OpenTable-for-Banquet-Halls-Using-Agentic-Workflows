"""Guardrails: the rules that keep the agents safe, honest and affordable, enforced in code.

Prompts *describe* these rules so the agents usually comply first time, but nothing
here depends on a model obeying. Each guardrail is a plain function or small class with
its own tests (``tests/test_guardrails.py``), and each is wired in at one place:

=====  ===========================  =================================================
 #     Guardrail                    Where it's enforced
=====  ===========================  =================================================
 G1    Customer input check         ``ConciergeSession.send`` before any model call
 G2    Tool-argument validation     ``Agent._execute`` before a tool handler runs
 G3    Hold limits                  ``ConciergeSession._place_hold``
 G4    Price grounding              ``ConciergeSession.send`` after the Concierge replies
 G5    Privacy (email redaction)    ``harness.trace.Recorder`` and logs
 G6    Token budgets                ``Agent.run`` before every model call
 G7    Human-only confirm/cancel    No agent has those tools (``team.py``); API only
=====  ===========================  =================================================

Host-safety rules from CLAUDE.md (no ``os``, confined paths, no deletions) live in
:mod:`banquet.paths` and are checked by ``tests/test_project_guardrails.py``.
"""

import json
import re
import threading
from dataclasses import dataclass

# --------------------------------------------------------------------------- G1 input

MAX_MESSAGE_CHARS = 4000

# Phrasing that addresses the model's instructions instead of describing an event.
# An override verb must be followed directly by the *assistant's* instructions: "your ...",
# "previous/prior/above/system ... instructions", or "the rules above". So ordinary
# sentences pass: "please ignore the previous date", "don't forget the house rules
# about candles", "can we override the noise rules?".
_VERB = r"\b(?:ignore|disregard|forget|override)\b\s+(?:all\s+|any\s+)?(?:of\s+)?"
_TARGET = r"(?:instructions?|rules|prompts?|guardrails?)\b"
_INJECTION_RE = re.compile(
    _VERB + r"(?:(?:your|the|these|my)\s+)?(?:previous|prior|above|earlier|system|original|existing)\s+" + _TARGET
    + r"|" + _VERB + r"your\s+" + _TARGET
    + r"|" + _VERB + r"(?:the\s+)?" + _TARGET + r"\s+above\b"
    + r"|\bsystem prompt\b|\byou are now\b|\bdeveloper mode\b|\bjailbreak\b"
    # "new instructions" only when aimed at the assistant ("here are your new instructions"),
    # not "new instructions for the caterer".
    r"|\byour new instructions?\b|\bnew (system|assistant) instructions?\b"
    r"|\bact as (the |an? )?(admin|administrator|developer|system)\b",
    re.IGNORECASE)
# Control characters other than tab and newline (they can hide text in logs and UIs).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

INJECTION_REPLY = ("I can only help with finding, pricing and reserving event venues, and I can't "
                   "change how I work. Tell me about your event (what it is, roughly how many "
                   "guests, where and when) and I'll find some options.")


@dataclass
class InputCheck:
    """Result of :func:`check_customer_message`.

    Attributes:
        ok: Whether the message may go to the Concierge.
        text: The cleaned message (control characters removed, trimmed).
        reason: Why it was refused (``None`` when ok), for the trace.
        reply: What to tell the customer when refused.
    """
    ok: bool
    text: str
    reason: str | None = None
    reply: str | None = None


def check_customer_message(text):
    """G1: clean a customer message and refuse prompt-injection attempts.

    Refused messages never reach a model, so they cost nothing.

    Args:
        text: The raw message.

    Returns:
        An :class:`InputCheck`.
    """
    clean = _CONTROL_RE.sub("", str(text)).strip()
    if not clean:
        return InputCheck(False, clean, "empty", "Tell me a little about the event you're planning.")
    if len(clean) > MAX_MESSAGE_CHARS:
        return InputCheck(False, clean[:MAX_MESSAGE_CHARS], "too_long",
                          f"That message is too long. Please keep it under {MAX_MESSAGE_CHARS} characters.")
    if _INJECTION_RE.search(clean):
        return InputCheck(False, clean, "prompt_injection", INJECTION_REPLY)
    return InputCheck(True, clean)


# --------------------------------------------------------------------------- G2 tool arguments

_JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
               "array": list, "object": dict}


def validate_args(schema, value, path="input"):
    """G2: check a tool call's arguments against the tool's JSON schema.

    Supports the subset of JSON Schema the tools use: ``type``, ``properties``,
    ``required``, ``additionalProperties: false``, ``minimum``/``maximum``,
    ``minItems``/``maxItems``, ``items`` and ``maxLength``. The model is *asked* to
    follow the schema; this makes sure a malformed call never reaches the booking code.

    Args:
        schema: The tool's ``input_schema``.
        value: The arguments the model sent.
        path: Where in the input we are (used in error messages).

    Returns:
        A list of problems, each naming the field and the fix. Empty means valid.
    """
    problems = []
    expected = schema.get("type")
    if expected:
        py_type = _JSON_TYPES[expected]
        # bool is a subclass of int in Python, but true/false is not a valid number in JSON Schema.
        if not isinstance(value, py_type) or (expected in ("integer", "number") and isinstance(value, bool)):
            return [f"{path} must be {'an' if expected[0] in 'aeiou' else 'a'} {expected}"]
    if expected in ("integer", "number"):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{path} must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{path} must be at most {schema['maximum']}")
    if expected == "string" and "maxLength" in schema and len(value) > schema["maxLength"]:
        problems.append(f"{path} must be at most {schema['maxLength']} characters")
    if expected == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            problems.append(f"{path} needs at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            problems.append(f"{path} allows at most {schema['maxItems']} items")
        for i, item in enumerate(value):
            problems += validate_args(schema.get("items", {}), item, f"{path}[{i}]")
    if expected == "object":
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                problems.append(f"missing required field '{name}'")
        for name, item in value.items():
            if name in props:
                problems += validate_args(props[name], item, name)
            elif schema.get("additionalProperties") is False:
                problems.append(f"unexpected field '{name}' (allowed: {', '.join(props) or 'none'})")
    return problems


# --------------------------------------------------------------------------- G3 holds

MAX_ACTIVE_HOLDS = 3


def email_was_given(email, customer_texts):
    """G3: true if ``email`` is exactly (ignoring case) an email address the customer typed.

    ``place_hold`` needs the customer's email. Requiring it to come from the customer's
    own words means an agent can't invent one or reuse another customer's. The match is
    against whole addresses extracted from the messages, so typing
    ``maria.lopez@example.com`` does not approve ``lopez@example.com``.

    Args:
        email: The email the agent passed to ``place_hold``.
        customer_texts: Every message the customer sent in this session.

    Returns:
        ``True`` if the email was typed by the customer.
    """
    wanted = (email or "").strip().lower()
    typed = {m.lower() for t in customer_texts for m in _EMAIL_RE.findall(t)}
    return bool(wanted) and wanted in typed


# --------------------------------------------------------------------------- G4 price grounding

# "$58,412.50", "$165", "$60k", "$1.2M", "$ 900"
_MONEY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s?([kKmM])?(?![\w])")
#: JSON fields in tool results that hold dollar amounts. Only these (and $-amounts in text)
#: count as evidence, so a guest count of 180 can't "ground" an invented "$180 per guest".
MONEY_FIELDS = {"total", "amount", "per_guest", "deposit_due", "per_guest_price", "rental_fee",
                "quote_total", "max_price_per_guest"}


def money_amounts(text):
    """Find dollar amounts in text, with the precision each is written to.

    The precision matters for grounding: "$58k" only claims the value to the nearest
    $1,000, so it's consistent with a quote of $58,412.50; "$58,400" claims the nearest
    dollar, so it isn't.

    Args:
        text: Any text.

    Returns:
        A list of ``(value, unit, raw_text)``. ``unit`` is the value of the last
        written digit (1 for "$165", 0.01 for "$1.50", 1000 for "$60k").
    """
    found = []
    for m in _MONEY_RE.finditer(text):
        whole, decimals, suffix = m.group(1).replace(",", ""), m.group(2) or "", (m.group(3) or "").lower()
        scale = {"k": 1_000, "m": 1_000_000}.get(suffix, 1)
        value = float(f"{whole}.{decimals}" if decimals else whole) * scale
        unit = scale / (10 ** len(decimals))
        found.append((value, unit, m.group(0).strip()))
    return found


def _money_fields(value, out):
    """Collect the numbers stored under :data:`MONEY_FIELDS` anywhere in a JSON value."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k in MONEY_FIELDS and isinstance(v, (int, float)) and not isinstance(v, bool):
                out.add(float(v))
            else:
                _money_fields(v, out)
    elif isinstance(value, list):
        for v in value:
            _money_fields(v, out)


def evidence_numbers(texts):
    """Collect the dollar amounts in tool results and customer messages (the grounding evidence).

    Evidence is only money: ``$``-amounts written in any text (customer messages,
    specialists' replies) and the money fields (:data:`MONEY_FIELDS`) of JSON tool
    results. Other numbers (guest counts, ids, dates, percentages) never count.

    Args:
        texts: Strings: tool results (JSON or a specialist's text) and customer messages.

    Returns:
        A set of floats. Dollar amounts written with k/M are expanded.
    """
    numbers = set()
    for t in texts:
        for value, _, _ in money_amounts(t):
            numbers.add(value)
        try:
            _money_fields(json.loads(t), numbers)
        except (ValueError, TypeError):
            continue
    return numbers


def ungrounded_amounts(reply, evidence_texts):
    """G4: list the dollar amounts in a reply that no tool result or customer message supports.

    An amount is grounded if some evidence number rounds to it at the precision it's
    written with, i.e. ``|amount - evidence| <= unit / 2``.

    Limit: this checks the Concierge's reply against what it was told (tool results,
    including the specialists' replies). Arithmetic a specialist does in its own reply
    (for example "saves $4,312") counts as evidence once it's in a tool result.

    Args:
        reply: The Concierge's reply to the customer.
        evidence_texts: Tool-result contents and customer messages from this session.

    Returns:
        The raw text of each unsupported amount, e.g. ``["$55,000"]``.
    """
    evidence = evidence_numbers(evidence_texts)
    bad = []
    for value, unit, raw in money_amounts(reply):
        if not any(abs(value - e) <= unit / 2 + 1e-6 for e in evidence):
            bad.append(raw)
    return bad


GROUNDING_NOTE = ("[Guardrail] Your last reply contains dollar amounts that don't appear in any quote or "
                  "tool result in this conversation: {amounts}. Rewrite the reply using only figures "
                  "from tool results (ask the Pricing Analyst for a quote if you need a new number), "
                  "and don't mention this note to the customer.")
GROUNDING_FALLBACK = ("\n\n*Note: please rely only on prices shown in a formal quote; ask me for an exact "
                      "quote for any option.*")


# --------------------------------------------------------------------------- G5 privacy

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def redact(value):
    """G5: replace email addresses with ``"<email>"`` anywhere in a JSON-like value.

    Used for everything written to traces, run reports and the PDF guide.

    Args:
        value: A str, dict, list or other JSON-compatible value.

    Returns:
        A redacted copy (the input is not modified).
    """
    if isinstance(value, str):
        return _EMAIL_RE.sub("<email>", value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


# --------------------------------------------------------------------------- G6 token budgets

#: Tokens (input + output + cache) one customer turn may use, across all agents.
TURN_TOKEN_BUDGET = 400_000
#: Tokens a whole chat session may use.
SESSION_TOKEN_BUDGET = 2_000_000


class BudgetExceeded(RuntimeError):
    """A turn or session used up its token budget; the build stops before the next model call."""


class TokenBudget:
    """G6: per-turn and per-session token budgets shared by every agent in a session.

    The runtime calls :meth:`check` before each model call and :meth:`charge` after it.
    A runaway loop (for example an agent retrying a failing tool forever) therefore
    stops at a known cost instead of running until the turn limit.

    Limit: the two specialists can run in parallel, and each checks the budget only
    before its own call, so a turn can overshoot by the size of calls already in flight.
    Counting is thread-safe (a lock guards the counters).

    Args:
        turn_limit: Tokens allowed per customer turn.
        session_limit: Tokens allowed for the whole session.
    """

    def __init__(self, turn_limit=TURN_TOKEN_BUDGET, session_limit=SESSION_TOKEN_BUDGET):
        """Start with nothing used (see the class docstring for the arguments)."""
        self.turn_limit = turn_limit
        self.session_limit = session_limit
        self.turn_used = 0
        self.session_used = 0
        self._lock = threading.Lock()

    def start_turn(self):
        """Reset the per-turn count at the start of a customer turn."""
        self.turn_used = 0

    def check(self):
        """Raise :class:`BudgetExceeded` if either budget is already used up."""
        if self.turn_used >= self.turn_limit:
            raise BudgetExceeded(f"This request used its token budget ({self.turn_limit:,} tokens).")
        if self.session_used >= self.session_limit:
            raise BudgetExceeded(f"This conversation used its token budget ({self.session_limit:,} tokens).")

    def charge(self, tokens):
        """Add ``tokens`` (all input, output and cache tokens of one model call) to both counts."""
        with self._lock:
            self.turn_used += tokens
            self.session_used += tokens
