"""FastAPI app: chat with the Concierge, browse venues, confirm or cancel holds.

Run with the launcher, which binds to 127.0.0.1 and keeps caches in the project::

    .\\run.ps1 serve                 # live agents (needs ANTHROPIC_API_KEY)
    .\\run.ps1 serve --simulated     # scripted stand-in for Claude, no key, no cost
    .\\run.ps1 serve --db data\\other.db

``uvicorn banquet.api:app`` also works and uses the default database.

There's no ``import os`` here (CLAUDE.md). The database path is a ``create_app``
argument set by :mod:`banquet.serve`, not an environment variable; the Anthropic SDK
reads ``ANTHROPIC_API_KEY`` itself.
"""

import logging
import secrets
import threading
from collections import OrderedDict

import anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from banquet.agents import AgentError, ConciergeSession
from banquet.db import DEFAULT_DB_PATH, connect, init_db
from banquet.guardrails import MAX_MESSAGE_CHARS, BudgetExceeded
from banquet.paths import PROJECT_DIR
from banquet.services import BanquetService, BookingError

log = logging.getLogger(__name__)
STATIC = PROJECT_DIR / "banquet" / "static"
MAX_SESSIONS = 500

# Security headers on every response. The page uses one inline <script> and <style>
# block, so the CSP allows inline code from this page but nothing from other origins:
# no external scripts, frames, or network calls except back to this server.
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


class ChatRequest(BaseModel):
    """Body of ``POST /api/chat``. Length limits match guardrail G1."""
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    session_id: str | None = Field(default=None, max_length=64)


class CustomerAction(BaseModel):
    """Body of the confirm/cancel endpoints: the email the hold was placed with."""
    email: str = Field(min_length=3, max_length=254)


def create_app(db_path=DEFAULT_DB_PATH, client=None):
    """Build the FastAPI app.

    Args:
        db_path: SQLite database path inside the project (or ``":memory:"``).
        client: The Claude client. Defaults to ``anthropic.Anthropic()``; tests and the
            ``--simulated`` mode pass a stand-in.

    Returns:
        A configured :class:`fastapi.FastAPI` app.

    Side effects:
        Opens (and if needed creates and seeds) the database.
    """
    conn = connect(db_path)
    init_db(conn)
    service = BanquetService(conn)
    client = client or anthropic.Anthropic()
    sessions = OrderedDict()
    sessions_lock = threading.Lock()

    app = FastAPI(title="Banquet Concierge")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        """Add :data:`SECURITY_HEADERS` to every response."""
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    def get_session(session_id):
        """Return ``(id, session)``: the existing session, or a new one with a random id.

        Sessions are kept in memory, least recently used first; beyond
        :data:`MAX_SESSIONS` the oldest is dropped (its holds stay in the database).
        """
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
        """Serve the chat page."""
        return FileResponse(STATIC / "index.html")

    @app.post("/api/chat")
    def chat(req: ChatRequest):
        """Run one customer turn and return the reply, venue cards, holds and the agent trace."""
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
        except BudgetExceeded as exc:
            raise HTTPException(429, f"{exc} Please start a new conversation or simplify the request.")
        except AgentError as exc:
            raise HTTPException(504, str(exc))
        return {"session_id": session_id, **result}

    @app.get("/api/venues")
    def venues(guest_count: int | None = None, city: str | None = None, state: str | None = None,
               style: str | None = None, cuisine: str | None = None):
        """Browse venues with optional filters (state accepts a name or code)."""
        try:
            return service.search_venues(guest_count=guest_count, city=city, state=state,
                                         style=style, cuisine=cuisine)
        except BookingError as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/venues/{venue_id}")
    def venue(venue_id: int):
        """Return one venue's full details."""
        try:
            return service.get_venue(venue_id)
        except BookingError as exc:
            raise HTTPException(404, str(exc))

    @app.get("/api/sessions/{session_id}/holds")
    def holds(session_id: str):
        """List the holds placed in a chat session."""
        with sessions_lock:
            session = sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "Unknown session.")
        return session.holds()

    # Confirming and cancelling are human-only actions (guardrail G7): no agent has these tools.
    @app.post("/api/bookings/{booking_id}/confirm")
    def confirm(booking_id: int, action: CustomerAction):
        """Customer confirms a hold (email must match)."""
        try:
            return service.confirm_booking(booking_id, action.email)
        except BookingError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/bookings/{booking_id}/cancel")
    def cancel(booking_id: int, action: CustomerAction):
        """Customer cancels a booking or releases a hold (email must match)."""
        try:
            return service.cancel_booking(booking_id, action.email)
        except BookingError as exc:
            raise HTTPException(400, str(exc))

    return app


def __getattr__(name):
    """Create ``app`` lazily on first access, so importing this module needs no API key.

    This keeps ``uvicorn banquet.api:app`` working while tests can import
    :func:`create_app` without a key.
    """
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
