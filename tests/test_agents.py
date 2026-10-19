"""Agent loop tests with a scripted fake client, so no API calls are made."""

from datetime import date, timedelta
from types import SimpleNamespace as NS

import pytest

from banquet.agents import AgentError, ConciergeSession
from banquet.agents.runtime import REFUSAL_REPLY, Agent, Tool
from banquet.db import connect, init_db
from banquet.services import BanquetService


def text(t):
    return NS(type="text", text=t)


def tool_use(id_, name, **input_):
    return NS(type="tool_use", id=id_, name=name, input=input_)


def response(*content, stop="end_turn"):
    return NS(content=list(content), stop_reason=stop)


class FakeClient:
    """Returns scripted responses in order and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        # Snapshot the messages: the agent keeps appending to the same list.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def echo_tool(calls):
    def handler(value):
        calls.append(value)
        return {"echo": value}
    return Tool("echo", "Echo a value.", {"type": "object", "properties": {"value": {"type": "string"}},
                                          "required": ["value"]}, handler)


def test_loop_runs_tool_and_returns_final_text():
    calls = []
    client = FakeClient(
        response(tool_use("t1", "echo", value="hi"), stop="tool_use"),
        response(text("done")),
    )
    agent = Agent(client, "Test", "sys", [echo_tool(calls)])
    messages = [{"role": "user", "content": "go"}]
    events = []

    assert agent.run(messages, on_event=events.append) == "done"
    assert calls == ["hi"]
    # History is append-only: user, assistant(tool_use), user(tool_result), assistant(text)
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    result = messages[2]["content"][0]
    assert result["tool_use_id"] == "t1" and '"echo": "hi"' in result["content"]
    assert [e["type"] for e in events] == ["tool_call", "tool_result"]

    request = client.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["fallbacks"] == "default"
    assert request["output_config"] == {"effort": "medium"}


def test_parallel_tool_calls_return_in_one_message():
    calls = []
    client = FakeClient(
        response(tool_use("a", "echo", value="1"), tool_use("b", "echo", value="2"), stop="tool_use"),
        response(text("ok")),
    )
    messages = [{"role": "user", "content": "go"}]
    Agent(client, "Test", "sys", [echo_tool(calls)]).run(messages)
    results = messages[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]
    assert sorted(calls) == ["1", "2"]


def test_bad_arguments_and_unknown_tools_become_error_results():
    client = FakeClient(
        response(tool_use("a", "echo", wrong="x"), tool_use("b", "nope"), stop="tool_use"),
        response(text("recovered")),
    )
    messages = [{"role": "user", "content": "go"}]
    assert Agent(client, "Test", "sys", [echo_tool([])]).run(messages) == "recovered"
    a, b = messages[2]["content"]
    assert a["is_error"] and "Invalid arguments" in a["content"]
    assert b["is_error"] and "Unknown tool" in b["content"]


def test_refusal_returns_safe_reply():
    client = FakeClient(response(stop="refusal"))
    assert Agent(client, "Test", "sys", []).run([{"role": "user", "content": "x"}]) == REFUSAL_REPLY


def test_turn_limit():
    client = FakeClient(*[response(tool_use(f"t{i}", "echo", value="x"), stop="tool_use") for i in range(3)])
    with pytest.raises(AgentError):
        Agent(client, "Test", "sys", [echo_tool([])], max_turns=3).run([{"role": "user", "content": "go"}])


@pytest.fixture
def service(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_db(conn, today=date.today())
    return BanquetService(conn)


def test_concierge_delegates_shows_cards_and_places_hold(service):
    venue = next(v for v in service.search_venues() if v["name"] == "The Copperleaf")
    hall_id = venue["halls"][1]["hall_id"]
    package_id = venue["packages"][0]["package_id"]
    event_date = (date.today() + timedelta(days=365)).isoformat()

    client = FakeClient(
        # Concierge delegates to the scout...
        response(tool_use("c1", "ask_venue_scout", brief="80 guests, Manhattan"), stop="tool_use"),
        # ...the scout searches and replies (fresh context)...
        response(tool_use("s1", "search_venues", guest_count=80, city="New York"), stop="tool_use"),
        response(text("Shortlist: The Copperleaf")),
        # ...the concierge shows the card, places the hold, then replies.
        response(tool_use("c2", "show_venues", venue_ids=[venue["venue_id"]]),
                 tool_use("c3", "place_hold", hall_id=hall_id, package_id=package_id,
                          event_date=event_date, guest_count=80, event_type="dinner",
                          customer_name="Ada", customer_email="ada@example.com"),
                 stop="tool_use"),
        response(text("Your hold is in place.")),
    )
    session = ConciergeSession(client, service)
    result = session.send("Dinner for 80 in Manhattan, please hold the Copperleaf. Ada, ada@example.com")

    assert result["reply"] == "Your hold is in place."
    assert [v["name"] for v in result["venues"]] == ["The Copperleaf"]
    assert len(result["holds"]) == 1 and result["holds"][0]["status"] == "hold"
    agents = {e["agent"] for e in result["events"]}
    assert agents == {"Concierge", "Venue Scout"}

    scout_request = client.requests[1]
    assert scout_request["messages"] == [{"role": "user", "content": "80 guests, Manhattan"}]
    assert scout_request["output_config"] == {"effort": "low"}
    # Specialists can read but never write.
    scout_tools = {t["name"] for t in scout_request["tools"]}
    assert "place_hold" not in scout_tools and "search_venues" in scout_tools
