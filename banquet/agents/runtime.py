"""A small agent runtime on top of the Claude Messages API.

Each Agent owns a system prompt and a set of tools, and runs the standard
tool-use loop: call Claude, run any tools it asks for, send the results back,
repeat until it answers in plain text.
"""

import inspect
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

import anthropic

from banquet.services import BookingError

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000

REFUSAL_REPLY = ("Sorry, I can't help with that request. "
                 "I'm happy to help you find and book a venue for your event.")


class AgentError(RuntimeError):
    pass


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Any]

    def to_param(self):
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema}


def text_of(content):
    return "\n".join(b.text for b in content if b.type == "text").strip()


class Agent:
    def __init__(self, client, name, system, tools, effort="medium", max_turns=15):
        self.client = client
        self.name = name
        self.system = system
        self.tools = {t.name: t for t in tools}
        self.tool_params = [t.to_param() for t in tools]
        self.effort = effort
        self.max_turns = max_turns

    def run(self, messages, on_event=None):
        """Advance the conversation until the agent replies in text.

        `messages` is appended to in place and never edited, so the caller can
        keep it as the conversation history for the next turn.
        """
        emit = on_event or (lambda event: None)
        for _ in range(self.max_turns):
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
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                emit({"agent": self.name, "type": "refusal"})
                return REFUSAL_REPLY

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                reply = text_of(response.content)
                if response.stop_reason == "max_tokens":
                    log.warning("%s hit max_tokens", self.name)
                return reply or "(no reply)"

            # Independent tool calls (e.g. asking two specialists) run concurrently.
            # All results go back in a single user message.
            with ThreadPoolExecutor(max_workers=len(tool_uses)) as pool:
                results = list(pool.map(lambda b: self._execute(b, emit), tool_uses))
            messages.append({"role": "user", "content": results})

        raise AgentError(f"{self.name} did not finish within {self.max_turns} turns.")

    def _execute(self, block, emit):
        emit({"agent": self.name, "type": "tool_call", "tool": block.name, "input": block.input})
        tool = self.tools.get(block.name)
        try:
            if tool is None:
                raise BookingError(f"Unknown tool {block.name!r}.")
            if not isinstance(block.input, dict):
                raise BookingError("Tool input must be a JSON object.")
            try:
                inspect.signature(tool.handler).bind(**block.input)
            except TypeError as exc:
                raise BookingError(f"Invalid arguments for {block.name}: {exc}") from None
            output = tool.handler(**block.input)
            content = output if isinstance(output, str) else json.dumps(output, default=str)
            emit({"agent": self.name, "type": "tool_result", "tool": block.name, "ok": True})
            return {"type": "tool_result", "tool_use_id": block.id, "content": content}
        except BookingError as exc:
            message = str(exc)
        except anthropic.APIError as exc:
            log.exception("Sub-agent call failed in %s", block.name)
            message = f"The specialist is unavailable right now ({type(exc).__name__})."
        except Exception:
            log.exception("Tool %s failed", block.name)
            message = "Internal error while running this tool."
        emit({"agent": self.name, "type": "tool_result", "tool": block.name, "ok": False,
              "error": message})
        return {"type": "tool_result", "tool_use_id": block.id, "content": message,
                "is_error": True}
