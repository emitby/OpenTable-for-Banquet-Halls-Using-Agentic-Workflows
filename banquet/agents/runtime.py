"""A small agent runtime on top of the Claude Messages API.

Each :class:`Agent` owns a system prompt and a set of tools, and runs the standard
tool-use loop: call Claude, run any tools it asks for, send the results back,
repeat until it answers in plain text.

What the runtime adds around the loop (the "harness hooks"):

* **G2 argument validation:** every tool call is checked against the tool's JSON
  schema before the handler runs; problems go back to the model as ``is_error``
  results naming the fix.
* **G6 token budget:** an optional :class:`~banquet.guardrails.TokenBudget` is checked
  before every model call and charged after it.
* **Events for tracing and token accounting:** ``on_event`` receives a ``model_call``
  event (usage, latency, stop reason, the tool calls requested and their sizes) for
  every model call, and ``tool_call``/``tool_result`` events (with ids, latency and
  result size) for every tool. :mod:`banquet.harness` turns these into traces and the
  per-tool token ledger. Each event carries the ``run`` id of the ``Agent.run`` call it
  came from and the ``parent`` tool call (if this run is a specialist answering a
  delegation), so a specialist's tokens can be attributed to the tool that called it.
"""

import contextvars
import inspect
import itertools
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

import anthropic

from banquet.guardrails import validate_args
from banquet.services import BookingError

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000

REFUSAL_REPLY = ("Sorry, I can't help with that request. "
                 "I'm happy to help you find and book a venue for your event.")

# The tool call currently being executed in this thread. A delegation tool's handler
# runs a specialist Agent in the same thread, so the specialist's run can read which
# tool call it is answering. ThreadPoolExecutor threads start with an empty context,
# and _execute sets this before calling the handler, so it's always the right call.
_current_call = contextvars.ContextVar("current_tool_call", default=None)
_run_ids = itertools.count(1)


class AgentError(RuntimeError):
    """An agent couldn't finish, e.g. it hit its turn limit."""


@dataclass
class Tool:
    """A tool an agent may call.

    Attributes:
        name: Tool name the model uses.
        description: What the tool does and when to use it (sent to the model).
        input_schema: JSON schema for the arguments. Also enforced in code (G2).
        handler: Python function called with the arguments as keyword arguments.
            Its return value (str, or anything JSON-serializable) becomes the result.
    """
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Any]

    def to_param(self):
        """Return the tool definition in the Messages API format."""
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema}


def text_of(content):
    """Join the text blocks of a model response's content into one string."""
    return "\n".join(b.text for b in content if b.type == "text").strip()


def usage_of(response):
    """Read token usage from a response, treating missing fields as 0.

    Args:
        response: A Messages API response (or a test double without ``usage``).

    Returns:
        ``{"input", "output", "cache_write", "cache_read"}`` token counts.
    """
    u = getattr(response, "usage", None)
    get = lambda name: int(getattr(u, name, 0) or 0) if u is not None else 0  # noqa: E731
    return {"input": get("input_tokens"), "output": get("output_tokens"),
            "cache_write": get("cache_creation_input_tokens"), "cache_read": get("cache_read_input_tokens")}


class Agent:
    """One Claude agent: a system prompt, tools, an effort level and a turn limit.

    Args:
        client: An ``anthropic.Anthropic`` client, or a test/simulated stand-in with the
            same ``beta.messages.create`` method.
        name: Display name used in events ("Concierge", "Venue Scout" ...).
        system: The system prompt.
        tools: The :class:`Tool` objects this agent may call (least privilege: only these).
        effort: ``output_config.effort`` ("low", "medium", ...).
        max_turns: Model calls allowed per :meth:`run` before :class:`AgentError`.
    """

    def __init__(self, client, name, system, tools, effort="medium", max_turns=15):
        """Store the configuration (see the class docstring)."""
        self.client = client
        self.name = name
        self.system = system
        self.tools = {t.name: t for t in tools}
        self.tool_params = [t.to_param() for t in tools]
        self.effort = effort
        self.max_turns = max_turns

    def run(self, messages, on_event=None, budget=None):
        """Advance the conversation until the agent replies in text.

        Args:
            messages: The conversation. It's appended to in place and never edited
                (append-only history keeps prompt caching effective), so the caller can
                keep it for the next turn.
            on_event: Optional callback receiving event dicts (see the module docstring).
            budget: Optional :class:`~banquet.guardrails.TokenBudget` (G6).

        Returns:
            The agent's final text reply (a safe fixed reply if the model refuses).

        Raises:
            AgentError: If the agent doesn't finish within ``max_turns`` model calls.
            banquet.guardrails.BudgetExceeded: If the token budget runs out.
            anthropic.APIError: On API failures (the caller decides what to show).
        """
        emit = on_event or (lambda event: None)
        run_id, parent = next(_run_ids), _current_call.get()
        base = {"agent": self.name, "run": run_id, "parent": parent}
        for turn in range(self.max_turns):
            if budget is not None:
                budget.check()
            started = time.perf_counter()
            response = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self.system,
                tools=self.tool_params,
                messages=messages,
                output_config={"effort": self.effort},
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
            usage = usage_of(response)
            if budget is not None:
                budget.charge(sum(usage.values()))
            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            emit({**base, "type": "model_call", "turn": turn, "usage": usage,
                  "stop_reason": response.stop_reason,
                  "latency_s": round(time.perf_counter() - started, 3),
                  "tool_uses": [{"id": b.id, "name": b.name,
                                 "input_chars": len(json.dumps(b.input, default=str))} for b in tool_uses]})

            if response.stop_reason == "refusal":
                emit({**base, "type": "refusal"})
                return REFUSAL_REPLY

            if response.stop_reason != "tool_use" or not tool_uses:
                reply = text_of(response.content)
                if response.stop_reason == "max_tokens":
                    log.warning("%s hit max_tokens", self.name)
                return reply or "(no reply)"

            # Independent tool calls (e.g. asking two specialists) run concurrently.
            # All results go back in a single user message.
            with ThreadPoolExecutor(max_workers=len(tool_uses)) as pool:
                results = list(pool.map(lambda b: self._execute(b, emit, base), tool_uses))
            messages.append({"role": "user", "content": results})

        raise AgentError(f"{self.name} did not finish within {self.max_turns} turns.")

    def _execute(self, block, emit, base):
        """Run one tool call and turn its outcome into a ``tool_result`` block.

        Order of checks: known tool -> JSON object -> schema (G2) -> handler signature.
        Booking-rule violations and argument problems become ``is_error`` results the
        model can act on. Unexpected exceptions are logged and returned as a generic
        error, so internals never leak into the conversation.

        Args:
            block: The ``tool_use`` content block.
            emit: Event callback.
            base: The run's ``agent``/``run``/``parent`` fields for events.

        Returns:
            A ``tool_result`` content block dict.
        """
        emit({**base, "type": "tool_call", "id": block.id, "tool": block.name, "input": block.input})
        tool = self.tools.get(block.name)
        started = time.perf_counter()
        token = _current_call.set(block.id)
        try:
            if tool is None:
                raise BookingError(f"Unknown tool {block.name!r}.")
            if not isinstance(block.input, dict):
                raise BookingError("Tool input must be a JSON object.")
            problems = validate_args(tool.input_schema, block.input)
            if problems:
                raise BookingError(f"Invalid arguments for {block.name}: {'; '.join(problems)}.")
            try:
                inspect.signature(tool.handler).bind(**block.input)
            except TypeError as exc:
                raise BookingError(f"Invalid arguments for {block.name}: {exc}") from None
            output = tool.handler(**block.input)
            content = output if isinstance(output, str) else json.dumps(output, default=str)
            emit({**base, "type": "tool_result", "id": block.id, "tool": block.name, "ok": True,
                  "result_chars": len(content), "latency_s": round(time.perf_counter() - started, 3)})
            return {"type": "tool_result", "tool_use_id": block.id, "content": content}
        except BookingError as exc:
            message = str(exc)
        except anthropic.APIError as exc:
            log.exception("Sub-agent call failed in %s", block.name)
            message = f"The specialist is unavailable right now ({type(exc).__name__})."
        except Exception:
            log.exception("Tool %s failed", block.name)
            message = "Internal error while running this tool."
        finally:
            _current_call.reset(token)
        emit({**base, "type": "tool_result", "id": block.id, "tool": block.name, "ok": False,
              "error": message, "result_chars": len(message),
              "latency_s": round(time.perf_counter() - started, 3)})
        return {"type": "tool_result", "tool_use_id": block.id, "content": message,
                "is_error": True}
