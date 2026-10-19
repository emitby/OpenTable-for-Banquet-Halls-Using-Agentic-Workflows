"""FastAPI app: chat with the Concierge, browse venues, confirm or cancel holds.

Run with:  uvicorn banquet.api:app --reload
"""

import logging
import os
import secrets
import threading
from collections import OrderedDict
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from banquet.agents import AgentError, ConciergeSession
from banquet.db import DEFAULT_DB_PATH, connect, init_db
from banquet.services import BanquetService, BookingError

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"
MAX_SESSIONS = 500


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


class CustomerAction(BaseModel):
    email: str = Field(min_length=3, max_length=254)


def create_app(db_path=DEFAULT_DB_PATH, client=None):
    conn = connect(db_path)
    init_db(conn)
    service = BanquetService(conn)
    client = client or anthropic.Anthropic()
    sessions = OrderedDict()
    sessions_lock = threading.Lock()

    app = FastAPI(title="Banquet Concierge")

    def get_session(session_id):
        with sessions_lock:
            if session_id and session_id in sessions:
                sessions.move_to_end(session_id)
                return session_id, sessions[session_id]
            session_id = secrets.token_urlsafe(16)
            sessions[session_id] = ConciergeSession(client, service)
            while len(sessions) > MAX_SESSIONS:
                sessions.popitem(last=False)
            return session_id, sessions[session_id]

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.post("/api/chat")
    def chat(req: ChatRequest):
        session_id, session = get_session(req.session_id)
        try:
            result = session.send(req.message)
        except anthropic.AuthenticationError:
            raise HTTPException(502, "The AI service rejected our credentials. Check ANTHROPIC_API_KEY.")
        except anthropic.RateLimitError:
            raise HTTPException(429, "The concierge is busy. Please try again in a minute.")
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as exc:
            log.exception("Claude API error")
            raise HTTPException(502, f"The AI service is unavailable ({type(exc).__name__}).")
        except AgentError as exc:
            raise HTTPException(504, str(exc))
        return {"session_id": session_id, **result}

    @app.get("/api/venues")
    def venues(guest_count: int | None = None, city: str | None = None,
               style: str | None = None, cuisine: str | None = None):
        return service.search_venues(guest_count=guest_count, city=city, style=style, cuisine=cuisine)

    @app.get("/api/venues/{venue_id}")
    def venue(venue_id: int):
        try:
            return service.get_venue(venue_id)
        except BookingError as exc:
            raise HTTPException(404, str(exc))

    @app.get("/api/sessions/{session_id}/holds")
    def holds(session_id: str):
        with sessions_lock:
            session = sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "Unknown session.")
        return session.holds()

    # Confirming and cancelling are human-only actions: no agent has these tools.
    @app.post("/api/bookings/{booking_id}/confirm")
    def confirm(booking_id: int, action: CustomerAction):
        try:
            return service.confirm_booking(booking_id, action.email)
        except BookingError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/bookings/{booking_id}/cancel")
    def cancel(booking_id: int, action: CustomerAction):
        try:
            return service.cancel_booking(booking_id, action.email)
        except BookingError as exc:
            raise HTTPException(400, str(exc))

    return app


def _default_app():
    # Created lazily so importing this module (e.g. in tests) needs no API key.
    return create_app(os.environ.get("BANQUET_DB", DEFAULT_DB_PATH))


def __getattr__(name):
    if name == "app":
        global app
        app = _default_app()
        return app
    raise AttributeError(name)
