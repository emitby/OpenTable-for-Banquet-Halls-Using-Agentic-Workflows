"""Build the illustrated PDF guide: ``docs/how-it-works.pdf``.

    .\\run.ps1 install-browser    # once: Chromium into .cache\\ms-playwright
    .\\run.ps1 guide

Steps:

1. Run the scenario suite in **simulated** mode (free, offline) and keep its summary:
   per-scenario scores and the token ledger per agent and per tool.
2. Start the real web app on 127.0.0.1 with the :class:`SimulatedClient`, drive it with
   Playwright, and save screenshots to ``docs/images/``.
3. Write ``docs/guide/how-it-works.html``: the text, the functionality map and the
   agentic workflow chart (hand-laid SVG), the token charts (SVG drawn from the ledger),
   tables and screenshots.
4. Print that page to ``docs/how-it-works.pdf`` with Chromium.

Every token number in the guide comes from the simulated run and is labeled as an
estimate. Every output path is confined to the project; nothing is deleted.
"""

import html
import socket
import threading
import time
from datetime import date

from banquet.db import all_venues
from banquet.harness.run_scenarios import run_suite
from banquet.harness.scenarios import SCENARIOS
from banquet.paths import DOCS_DIR, confine, write_text
from banquet.states import STATES

IMAGES = DOCS_DIR / "images"
HTML_OUT = DOCS_DIR / "guide" / "how-it-works.html"
PDF_OUT = DOCS_DIR / "how-it-works.pdf"
VIEWPORT = {"width": 1280, "height": 860}

# Token-type colors for the stacked bars (identity, fixed order) and the single-series agent bars.
COLORS = {"decision": "#8a5a2b", "result": "#d9a441", "specialist": "#4f7cac"}
AGENT_COLOR = "#8a5a2b"
INK, MUTED, LINE = "#1f1d1a", "#6b655c", "#e6e0d6"
LABELS = {"decision": "Decision (to call the tool)", "result": "Result (to read its output)",
          "specialist": "Specialist (the delegated agent's own usage)"}


def esc(text):
    """HTML-escape any value for safe inclusion in the guide."""
    return html.escape(str(text))


# ----------------------------------------------------------------------------- screenshots

def _free_port():
    """Ask the OS for a free local port on 127.0.0.1."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    """Run the web app with a SimulatedClient in a background thread (context manager).

    Args:
        scenario_id: Which scenario script the simulated agents follow.
    """

    def __init__(self, scenario_id):
        """Prepare (but don't start) the server for one scenario."""
        import uvicorn

        from banquet.api import create_app
        from banquet.harness.simulated import SimulatedClient
        self.port = _free_port()
        app = create_app(":memory:", client=SimulatedClient(SCENARIOS[scenario_id]))
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        """Start the server and wait (up to 15 s) until it accepts requests."""
        self.thread.start()
        deadline = time.time() + 15
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("The guide's web server didn't start.")
            time.sleep(0.05)
        return f"http://127.0.0.1:{self.port}/"

    def __exit__(self, *exc):
        """Ask the server to stop and wait for its thread."""
        self.server.should_exit = True
        self.thread.join(timeout=10)


def _send(page, text, replies):
    """Type a message, send it, and wait until the ``replies``-th bot reply has arrived."""
    page.fill("#input", text)
    page.press("#input", "Enter")
    page.wait_for_function("n => document.querySelectorAll('.msg.bot:not(.typing)').length >= n", arg=replies)
    page.eval_on_selector_all("details.activity", "els => els.forEach(e => e.open = true)")
    page.evaluate("document.getElementById('log').scrollTop = 1e6")


def take_screenshots(browser):
    """Drive the web app through a full session and a guardrail session; save PNGs.

    Args:
        browser: A Playwright Chromium browser.

    Returns:
        ``{name: relative image path}`` for the guide.

    Side effects:
        Writes ``docs/images/0*.png``.
    """
    confine(IMAGES).mkdir(parents=True, exist_ok=True)
    shots = {}

    def snap(page, name, clip=None):
        """Save a viewport screenshot (optionally clipped) and remember its path."""
        page.screenshot(path=str(confine(IMAGES / f"{name}.png")), clip=clip)
        shots[name] = f"../images/{name}.png"

    turns = SCENARIOS["texas-wedding"]["turns"]
    with _Server("texas-wedding") as url:
        page = browser.new_page(viewport=VIEWPORT)
        page.on("dialog", lambda d: d.accept())       # the "Confirm booking?" prompt
        page.goto(url)
        snap(page, "01-start")
        _send(page, turns[0]["say"], 2)
        page.wait_for_selector("#venues .card")
        snap(page, "02-options")
        _send(page, turns[1]["say"], 3)
        page.wait_for_selector("#holds .card")
        snap(page, "03-hold")
        page.click("button[data-act=confirm]")
        page.wait_for_selector(".status.confirmed")
        snap(page, "04-confirmed")
        page.close()
    guard = SCENARIOS["off-topic-injection"]["turns"]
    with _Server("off-topic-injection") as url:
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(url)
        _send(page, guard[0]["say"], 2)
        _send(page, guard[1]["say"], 3)
        snap(page, "05-guardrail", clip={"x": 0, "y": 0, "width": 1280, "height": 560})
        page.close()
    return shots


# ----------------------------------------------------------------------------- SVG diagrams

def _box(x, y, w, h, lines, fill="#ffffff", stroke=INK, bold_first=True, size=13):
    """An SVG rounded box with centered lines of text (the first line bold)."""
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>']
    top = y + h / 2 - (len(lines) - 1) * (size + 3) / 2 + size / 3
    for i, line in enumerate(lines):
        weight = "700" if i == 0 and bold_first else "400"
        fs = size if i == 0 else size - 2
        out.append(f'<text x="{x + w / 2}" y="{top + i * (size + 3)}" text-anchor="middle" font-size="{fs}" '
                   f'font-weight="{weight}" fill="{INK}">{esc(line)}</text>')
    return "".join(out)


def _arrow(points, label=None, at=None, dashed=False, color=INK):
    """An SVG polyline with an arrowhead, plus an optional label at ``at``."""
    pts = " ".join(f"{x},{y}" for x, y in points)
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    out = f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.6"{dash} marker-end="url(#arrow)"/>'
    if label:
        lx, ly = at
        out += (f'<text x="{lx}" y="{ly}" font-size="11" fill="{MUTED}" text-anchor="middle" '
                f'paint-order="stroke" stroke="#fff" stroke-width="4">{esc(label)}</text>')
    return out


_DEFS = ('<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
         f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{INK}"/></marker></defs>')


def workflow_svg():
    """The agentic workflow chart: who calls whom, with each guardrail at the point it applies."""
    parts = [f'<svg viewBox="0 0 960 580" xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI, Arial, sans-serif" role="img" aria-label="Agentic workflow">', _DEFS]
    parts.append(f'<rect x="270" y="20" width="500" height="480" rx="14" fill="#faf7f2" stroke="{MUTED}" stroke-dasharray="6 5"/>')
    parts.append(f'<text x="520" y="40" text-anchor="middle" font-size="11" fill="{MUTED}">Agent team · G2 schema check on every tool call · G6 token budget per turn and session</text>')
    parts.append(_box(20, 230, 110, 60, ["Customer", "web, CLI or API"]))
    parts.append(_box(150, 236, 100, 48, ["G1", "input check"], fill="#fff4e0"))
    parts.append(_box(290, 210, 170, 100, ["Concierge", "medium effort", "talks, delegates, holds", "G3 hold limits"], fill="#f2ede5"))
    parts.append(_box(560, 60, 190, 80, ["Venue Scout", "low effort · read-only", "fresh context per brief"], fill="#eef3f8"))
    parts.append(_box(560, 380, 190, 80, ["Pricing Analyst", "low effort · read-only", "fresh context per brief"], fill="#eef3f8"))
    parts.append(_box(800, 210, 140, 100, ["BanquetService", "booking rules in code", "SQLite · 50 states + DC"]))
    parts.append(_box(290, 400, 170, 48, ["G4", "price grounding"], fill="#fff4e0"))
    parts.append(_arrow([(130, 260), (150, 260)]))
    parts.append(_arrow([(250, 260), (290, 260)]))
    parts.append(_arrow([(460, 230), (560, 110)], "ask_venue_scout", (500, 150)))
    parts.append(_arrow([(750, 100), (870, 100), (870, 210)]))
    parts.append(f'<text x="758" y="92" font-size="11" fill="{MUTED}">search · details · availability</text>')
    parts.append(_arrow([(460, 290), (560, 410)], "ask_pricing_analyst", (500, 372)))
    parts.append(_arrow([(750, 420), (870, 420), (870, 310)], "details · build_quote", (810, 440)))
    parts.append(_arrow([(460, 260), (800, 260)], "show_venues · check_availability · place_hold", (630, 250)))
    parts.append(_arrow([(375, 310), (375, 400)], "reply", (400, 360)))
    parts.append(_arrow([(290, 424), (75, 424), (75, 290)], "checked reply", (180, 414)))
    parts.append(_arrow([(55, 290), (55, 540), (900, 540), (900, 310)],
                        "confirm / cancel: customer only (G7), no agent has these tools", (480, 532), dashed=True))
    parts.append("</svg>")
    return "".join(parts)


def functionality_svg(n_venues):
    """The functionality map: the app's six areas around a central node."""
    cards = [
        ("Customer interfaces", ["Web chat: venue cards, holds panel", "Agent activity with token counts",
                                 "Terminal CLI: /holds, /confirm", "REST API: 6 endpoints",
                                 "Launchers: run.ps1 / run.sh"]),
        ("Agent team (Claude)", ["Concierge: talks, delegates, holds", "Venue Scout: search, details, dates",
                                 "Pricing Analyst: quotes, budget fit", "Specialists are read-only",
                                 "Fresh context per delegation"]),
        ("Booking rules (code, no AI)", ["Search in 50 states + DC", "Availability + nearest open dates",
                                         "Itemized quotes + state sales tax", "Free 72-hour holds",
                                         "Confirm / cancel by customer only"]),
        ("Guardrails G1-G7", ["G1 input + injection check", "G2 tool-argument schema", "G3 hold limits + email",
                              "G4 price grounding", "G5 email redaction · G6 token budgets",
                              "G7 confirm/cancel by humans only"]),
        ("Harness", ["trace.jsonl for every run", "Token ledger per agent and tool",
                     "6 scripted scenarios, 8 scorers", "Simulated (free) or live (cost cap)",
                     "Run folder runs/<id>/"]),
        ("Data and host safety", [f"SQLite: {n_venues} fictional venues", "Tests use in-memory databases",
                                  "All paths confined to the project", "No os module, no deletions",
                                  "Server bound to 127.0.0.1"]),
    ]
    parts = [f'<svg viewBox="0 0 960 640" xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI, Arial, sans-serif" role="img" aria-label="Functionality map">']
    cx, cy, cw, ch = 380, 290, 200, 60
    for i, (title, items) in enumerate(cards):
        col, row = i % 3, i // 3
        x, y, w, h = 20 + col * 320, 20 + row * 390, 280, 230
        ax, ay = x + w / 2, (y + h) if row == 0 else y
        parts.append(f'<line x1="{cx + cw / 2}" y1="{cy if row == 0 else cy + ch}" x2="{ax}" y2="{ay}" stroke="{MUTED}" stroke-width="1.4"/>')
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="#ffffff" stroke="{INK}" stroke-width="1.4"/>')
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="36" rx="12" fill="#f2ede5"/>'
                     f'<rect x="{x}" y="{y + 24}" width="{w}" height="12" fill="#f2ede5"/>')
        parts.append(f'<text x="{x + 14}" y="{y + 24}" font-size="14" font-weight="700" fill="{INK}">{esc(title)}</text>')
        for j, item in enumerate(items):
            parts.append(f'<text x="{x + 16}" y="{y + 62 + j * 28}" font-size="12.5" fill="{INK}">• {esc(item)}</text>')
    parts.append(_box(cx, cy, cw, ch, ["Banquet Concierge", "OpenTable-style booking"], fill="#8a5a2b", stroke="#8a5a2b"))
    parts.append(f'<text x="{cx + cw / 2}" y="{cy + 27}" text-anchor="middle" font-size="15" font-weight="700" fill="#ffffff">Banquet Concierge</text>'
                 f'<text x="{cx + cw / 2}" y="{cy + 45}" text-anchor="middle" font-size="11" fill="#ffffff">OpenTable-style booking</text>')
    parts.append("</svg>")
    return "".join(parts)


def stacked_bars_svg(tools):
    """Horizontal stacked bars: attributed tokens per tool, split into decision/result/specialist.

    Args:
        tools: ``ledger(...)["tools"]``.

    Returns:
        SVG markup. Bars are sorted by total; each bar ends with its total as a direct label.
    """
    rows = sorted(tools.items(), key=lambda kv: -kv[1]["total"])
    left, width, bar, gap = 170, 600, 22, 14
    top = 44
    height = top + len(rows) * (bar + gap) + 10
    peak = max(t["total"] for _, t in rows) or 1
    parts = [f'<svg viewBox="0 0 900 {height}" xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI, Arial, sans-serif" role="img" aria-label="Tokens by tool">']
    lx = left
    for key in ("decision", "result", "specialist"):           # legend, in stacking order
        parts.append(f'<rect x="{lx}" y="8" width="12" height="12" rx="2" fill="{COLORS[key]}"/>'
                     f'<text x="{lx + 18}" y="18" font-size="11.5" fill="{INK}">{esc(LABELS[key])}</text>')
        lx += 18 + len(LABELS[key]) * 6.2 + 22
    for i, (name, t) in enumerate(rows):
        y = top + i * (bar + gap)
        parts.append(f'<text x="{left - 10}" y="{y + bar * 0.7}" text-anchor="end" font-size="12.5" fill="{INK}">{esc(name)}</text>')
        x = left
        for key in ("decision", "result", "specialist"):
            w = width * t[key] / peak
            if w >= 0.5:
                # 2px white gap between segments so adjacent colors never touch.
                parts.append(f'<rect x="{x}" y="{y}" width="{max(w - 2, 0.5)}" height="{bar}" rx="3" fill="{COLORS[key]}"/>')
            x += w
        parts.append(f'<text x="{x + 6}" y="{y + bar * 0.7}" font-size="12" fill="{INK}">{t["total"]:,}</text>')
    parts.append("</svg>")
    return "".join(parts)


def agent_bars_svg(agents):
    """Horizontal bars of total tokens per agent (one series, so no legend)."""
    rows = sorted(agents.items(), key=lambda kv: -kv[1]["total"])
    left, width, bar, gap = 170, 600, 22, 14
    height = len(rows) * (bar + gap) + 10
    peak = max(a["total"] for _, a in rows) or 1
    parts = [f'<svg viewBox="0 0 900 {height}" xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI, Arial, sans-serif" role="img" aria-label="Tokens by agent">']
    for i, (name, a) in enumerate(rows):
        y = 6 + i * (bar + gap)
        w = width * a["total"] / peak
        parts.append(f'<text x="{left - 10}" y="{y + bar * 0.7}" text-anchor="end" font-size="12.5" fill="{INK}">{esc(name)}</text>'
                     f'<rect x="{left}" y="{y}" width="{w}" height="{bar}" rx="3" fill="{AGENT_COLOR}"/>'
                     f'<text x="{left + w + 6}" y="{y + bar * 0.7}" font-size="12" fill="{INK}">{a["total"]:,}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ----------------------------------------------------------------------------- the page

CSS = """
@page { size: Letter; margin: 0.55in 0.6in; }
* { box-sizing: border-box; }
body { font: 11pt/1.45 "Segoe UI", Arial, sans-serif; color: #1f1d1a; margin: 0; }
h1 { font-size: 26pt; margin: 0 0 6px; color: #8a5a2b; }
h2 { font-size: 17pt; margin: 0 0 10px; color: #8a5a2b; border-bottom: 2px solid #e6e0d6; padding-bottom: 4px; }
h3 { font-size: 12.5pt; margin: 16px 0 6px; }
p { margin: 0 0 8px; }
section { break-before: page; }
section.cover { break-before: auto; }
.lead { font-size: 13pt; color: #3d3a35; }
.muted { color: #6b655c; font-size: 9.5pt; }
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 18px 0; }
.stat { border: 1px solid #e6e0d6; border-radius: 10px; padding: 10px 12px; }
.stat b { display: block; font-size: 20pt; color: #8a5a2b; }
.banner { background: #fff4e0; border: 1px solid #e8c98a; border-radius: 8px; padding: 8px 12px; margin: 10px 0; font-size: 10pt; }
figure { margin: 10px 0 14px; break-inside: avoid; }
figure img { display: block; margin: 0 auto; max-width: 100%; max-height: 3.85in; width: auto;
             border: 1px solid #e6e0d6; border-radius: 6px; }
h2, h3 { break-after: avoid; }
tr, li { break-inside: avoid; }
.page-break { break-before: page; }
.workflow svg { width: 94%; display: block; margin: 0 auto; }
figcaption { font-size: 9.5pt; color: #6b655c; margin-top: 4px; }
svg { width: 100%; height: auto; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; margin: 6px 0 12px; }
th, td { border-bottom: 1px solid #e6e0d6; padding: 4px 6px; text-align: left; vertical-align: top; }
th { background: #f2ede5; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
code { font-family: Consolas, monospace; font-size: 9.5pt; background: #f5f2ec; padding: 0 3px; border-radius: 3px; }
ol li, ul li { margin-bottom: 3px; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
"""


def _table(headers, rows, numeric=()):
    """An HTML table; columns whose index is in ``numeric`` are right-aligned."""
    head = "".join(f'<th class="{"n" if i in numeric else ""}">{esc(h)}</th>' for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(f'<td class="{"n" if i in numeric else ""}">{c}</td>' for i, c in enumerate(r)) + "</tr>"
                   for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def build_html(summary, shots):
    """Assemble the guide's HTML from the simulated suite summary and the screenshots.

    Args:
        summary: The dict returned by :func:`~banquet.harness.run_scenarios.run_suite`.
        shots: ``{name: image path}`` from :func:`take_screenshots`.

    Returns:
        The full HTML document as a string.
    """
    led = summary["ledger"]
    n_venues = len(all_venues())
    passed = f"{summary['passed']}/{summary['total']}"
    fig = lambda name, caption: f'<figure><img src="{shots[name]}" alt="{esc(caption)}"><figcaption>{esc(caption)}</figcaption></figure>'  # noqa: E731
    sim = ('<div class="banner"><b>Simulated numbers.</b> These token counts come from the scripted '
           '<code>SimulatedClient</code>, which estimates tokens as text characters ÷ 4 and adds a fixed '
           'reasoning allowance. They show how the harness measures and attributes tokens, not what live '
           'Claude calls cost. Run <code>.\\run.ps1 scenarios --live --max-cost 5</code> for real numbers.</div>')

    tool_rows = [[f"<code>{esc(n)}</code>", t["calls"], f"{t['decision']:,}", f"{t['result']:,}",
                  f"{t['specialist']:,}", f"<b>{t['total']:,}</b>", f"${t['cost']:.4f}"]
                 for n, t in sorted(led["tools"].items(), key=lambda kv: -kv[1]["total"])]
    agent_rows = [[esc(n), a["calls"], f"{a['input']:,}", f"{a['cache_read']:,}", f"{a['output']:,}",
                   f"<b>{a['total']:,}</b>", f"${a['cost']:.4f}"] for n, a in led["agents"].items()]
    sc_rows = []
    for sid, r in summary["scenarios"].items():
        ok = sum(s["ok"] for s in r["scores"].values())
        fired = sorted({gname.split(":")[0] for t in r["transcript"] for gname in t["guardrails"]})
        sc_rows.append([f"<b>{esc(sid)}</b><br><span class='muted'>{esc(r['title'])}</span>",
                        len(r["transcript"]), r["ledger"]["totals"]["model_calls"],
                        f"{r['ledger']['totals']['tokens']:,}", f"${r['ledger']['totals']['cost']:.4f}",
                        esc(", ".join(fired) or "none"), f"{ok}/{len(r['scores'])}"])

    guard_rows = [
        ["G1", "Customer input", "Strips control characters, limits length, and politely refuses prompt-injection phrasing before any model call (zero cost).", "<code>ConciergeSession.send</code>"],
        ["G2", "Tool arguments", "Every tool call is checked against the tool's JSON schema (types, ranges, required fields, no extra fields) before it runs; problems go back to the agent as fixable errors.", "<code>Agent._execute</code>"],
        ["G3", "Holds", "At most 3 active holds per conversation, and the email must be one the customer actually typed, so an agent can't invent or reuse one.", "<code>_place_hold</code>"],
        ["G4", "Price grounding", "Every dollar amount in the Concierge's reply must match a dollar amount from a tool result (a money field such as a quote total, or a $-figure in a specialist's reply) or the customer's own words, at the precision written. Guest counts, ids and percentages never count. Otherwise the reply is sent back once; if it's still wrong, a warning is added.", "<code>ConciergeSession.send</code>"],
        ["G5", "Privacy", "Emails are replaced with &lt;email&gt; in traces and run reports. (Customers still see their own email on their own screen.)", "<code>harness.trace</code>"],
        ["G6", "Token budgets", "400,000 tokens per customer turn and 2,000,000 per conversation, checked before every model call.", "<code>Agent.run</code>"],
        ["G7", "Human-only commitments", "No agent has a confirm or cancel tool. Only the customer can confirm (API, web button or CLI after y/N).", "<code>team.py</code>, <code>api.py</code>"],
    ]
    agents_tools = [
        ["Concierge", "medium", "ask_venue_scout, ask_pricing_analyst, check_availability, show_venues, place_hold", "Talks to the customer, delegates, shows cards, places holds"],
        ["Venue Scout", "low", "search_venues, get_venue_details, check_availability", "Finds and shortlists venues in the right state (read-only)"],
        ["Pricing Analyst", "low", "get_venue_details, build_quote", "Prices options and compares them to the budget (read-only)"],
    ]

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Banquet Concierge: how it works</title>
<style>{CSS}</style></head><body>

<section class="cover">
<h1>Banquet Concierge</h1>
<p class="lead">An OpenTable-style booking app for banquet halls anywhere in the United States, run by a team of
cooperating Claude agents, with guardrails enforced in code and a harness that traces every call and accounts
for every token.</p>
<p class="muted">How it works · generated {date.today():%B %d, %Y} by <code>.\\run.ps1 guide</code></p>
<div class="stats">
<div class="stat"><b>{len(STATES) - 1} + DC</b>states with venues</div>
<div class="stat"><b>{n_venues}</b>fictional venues</div>
<div class="stat"><b>3</b>Claude agents</div>
<div class="stat"><b>7</b>guardrails in code</div>
<div class="stat"><b>{passed}</b>scenarios passed (simulated)</div>
<div class="stat"><b>{led['totals']['tokens']:,}</b>tokens across the suite, incl. cache reads (simulated)</div>
</div>
<h3>What it does</h3>
<p>A customer describes an event in plain language, for example <i>"a wedding for about 180 guests in Austin, Texas
next October, budget around $45,000"</i>. The <b>Concierge</b> agent works out what's needed and delegates: the
<b>Venue Scout</b> searches venues in that state and checks dates, and the <b>Pricing Analyst</b> builds itemized
quotes with that state's sales tax. The customer sees venue cards and exact quotes, can ask for a free 72-hour
hold, and confirms the booking themselves. No agent can commit the customer to a purchase.</p>
<h3>In this guide</h3>
<ol><li>A session, screen by screen</li><li>Functionality map</li><li>Agentic workflow</li><li>Guardrails</li>
<li>The harness</li><li>Tokens per tool and per agent</li><li>Running it</li></ol>
</section>

<section>
<h2>1. A session, screen by screen</h2>
<p>These screenshots are the real web app, driven by Playwright, with the scripted <code>SimulatedClient</code> in
place of Claude (so the agents' wording is scripted, but every tool result, card, hold and guardrail is real).</p>
{fig("01-start", "The start screen: describe the event or pick a suggestion. Venues are available in every state.")}
{fig("02-options", "The Venue Scout found Austin venues and the Pricing Analyst quoted them. 'Agent activity' lists each step and the turn's token count.")}
{fig("03-hold", "The customer asked for a hold and gave an email. The Concierge placed a free 72-hour hold; it appears in the Holds panel.")}
{fig("04-confirmed", "Only the customer can confirm: the button calls the API directly. No agent has a confirm tool (guardrail G7).")}
</section>

<section>
<h2>2. Functionality map</h2>
<p>Everything the program does, grouped into six areas.</p>
{functionality_svg(n_venues)}
<h3>Interfaces at a glance</h3>
{_table(["Interface", "What it offers"], [
    ["Web chat <code>.\\run.ps1 serve</code>", "Chat, venue cards, holds panel with Confirm / Release, agent activity with tokens"],
    ["Terminal <code>.\\run.ps1 cli --verbose</code>", "Chat, <code>/holds</code>, <code>/confirm &lt;id&gt;</code> after y/N, per-call token printout"],
    ["REST API", "<code>POST /api/chat</code>, <code>GET /api/venues?state=TX</code>, <code>GET /api/venues/{id}</code>, <code>GET /api/sessions/{id}/holds</code>, <code>POST /api/bookings/{id}/confirm</code> and <code>/cancel</code>"],
    ["Harness <code>.\\run.ps1 scenarios</code>", "Six scripted conversations, eight scorers, a token ledger, simulated or live"],
])}
</section>

<section>
<h2>3. Agentic workflow</h2>
<div class="workflow"><p>One customer turn: the Concierge delegates to the specialists (in parallel when it needs both), each specialist
works in a fresh context and returns only a summary, and every reply passes the price-grounding check before the
customer sees it. Guardrails are shown where they apply.</p>
{workflow_svg()}</div>
{_table(["Agent", "Effort", "Tools (least privilege)", "Job"], agents_tools)}
<h3>A turn, step by step</h3>
<ol>
<li><b>G1</b> checks the message. Injection attempts are answered without any model call.</li>
<li>The <b>Concierge</b> calls <code>ask_venue_scout</code> with a self-contained brief (state, city, guests, date, budget).</li>
<li>The <b>Venue Scout</b> runs <code>search_venues</code> (with the <code>state</code> filter) and <code>check_availability</code>, and returns a shortlist with ids.</li>
<li>The Concierge calls <code>show_venues</code> (cards) and <code>ask_pricing_analyst</code> together; the <b>Pricing Analyst</b> runs <code>build_quote</code> for each option.</li>
<li>The Concierge writes the reply; <b>G4</b> checks every dollar amount against the tool results. On request it
places a hold (<b>G3</b> checks); confirming is the customer's action only (<b>G7</b>).</li>
</ol>
</section>

<section>
<h2>4. Guardrails</h2>
<p>Prompts describe the rules so the agents usually comply first time, but every guardrail is enforced in code and
tested in both directions (the bad case is blocked, and the harmless look-alike, such as "please ignore the
previous date", still works).</p>
{_table(["#", "Guardrail", "What it does", "Where"], guard_rows)}
<p class="muted">Host safety (CLAUDE.md): no <code>os</code> module, paths and temporary files kept in the project folder,
no file deletions, the server bound to 127.0.0.1, and security headers on every response. Source scans in the test suite enforce these.</p>
{fig("05-guardrail", "An off-topic request is politely declined, and an injection attempt is stopped by G1 before any agent runs; the activity panel shows the guardrail.")}
</section>

<section>
<h2>5. The harness</h2>
<p>The harness wraps the agent team without changing it. The runtime emits an event for every model call and tool
call; the harness records, measures and scores them.</p>
{_table(["Part", "What it does"], [
    ["Runtime events", "<code>model_call</code> (agent, usage, latency, stop reason, tools requested and their argument sizes), <code>tool_call</code>, <code>tool_result</code> (ok/error, latency, result size) and <code>guardrail</code> events. A specialist's events carry the id of the delegation call that started it."],
    ["Trace", "<code>runs/&lt;id&gt;/&lt;scenario&gt;/trace.jsonl</code>: every event, emails redacted (G5)."],
    ["Token ledger", "Exact usage per agent; attributed usage per tool (see below); cost at the configured claude-opus-5-5 prices."],
    ["Scenarios", "Six scripted conversations: Texas wedding with a hold, Ohio corporate dinner, NYC gala on a booked date, a Denver price guess (G4), a hold without an email (G3), an off-topic request and an injection (G1)."],
    ["Scorers", "completed · venues_in_state · prices_grounded · expected_guardrails · holds · read_only_specialists · no_tools_when_declining · within_budget"],
    ["Modes", "<b>Simulated</b>: free, offline, scripted agents, estimated tokens. <b>Live</b>: real Claude agents, real usage, a cost cap (default $5) checked between scenarios. In live mode, G3 and G4 may fire but aren't required, since real agents rarely misbehave on cue."],
])}
<h3>How tokens are attributed to tools</h3>
<p>The API reports usage per model call, not per tool, so the ledger attributes it:</p>
<ul>
<li><b>Decision</b>: the output tokens of the call that requested the tool (reasoning plus writing the call), split by argument size when several tools are requested at once.</li>
<li><b>Result</b>: how much the agent's prompt grew on its next call, minus the previous output, split by result size: the cost of reading the tool's output.</li>
<li><b>Specialist</b>: for <code>ask_venue_scout</code> and <code>ask_pricing_analyst</code>, everything the delegated agent used.</li>
</ul>
<p>Per-agent totals are exact sums of reported usage; per-tool numbers are an attribution. If an API version leaves earlier reasoning out of later prompts, result tokens are understated (the subtraction assumes the previous output stays in the history).</p>
</section>

<section>
<h2>6. Tokens per tool and per agent</h2>
{sim}
<h3>Tokens attributed to each tool (whole suite)</h3>
{stacked_bars_svg(led["tools"])}
{_table(["Tool", "Calls", "Decision", "Result", "Specialist", "Total", "Est. cost"], tool_rows, numeric=(1, 2, 3, 4, 5, 6))}
<p class="muted">The delegation tools are the largest because they include the specialists' whole runs. Among the
data tools, the ones with the biggest results (for example <code>search_venues</code> and <code>build_quote</code>)
cost the most to read.</p>
<h3 class="page-break">Tokens per agent (simulated; exact sums of the usage each call reported)</h3>
{agent_bars_svg(led["agents"])}
{_table(["Agent", "Model calls", "Input", "Cache read", "Output", "Total", "Est. cost"], agent_rows, numeric=(1, 2, 3, 4, 5, 6))}
<p class="muted">Suite total: {led['totals']['model_calls']} model calls, {led['totals']['tool_calls']} tool calls,
{led['totals']['tokens']:,} tokens (including cache reads), ${led['totals']['cost']:.4f} at the configured prices
(input $4, output $20, cache write $5, cache read $0.20 per million tokens; check current pricing).</p>
<h3>Tokens and scores per scenario (simulated)</h3>
{_table(["Scenario", "Turns", "Model calls", "Tokens", "Est. cost", "Guardrails fired", "Scorers"], sc_rows, numeric=(1, 2, 3, 4))}
</section>

<section>
<h2>7. Running it</h2>
{_table(["Command", "What it does"], [
    ["<code>.\\run.ps1 serve</code>", "Web app on http://127.0.0.1:8000 with live agents (needs ANTHROPIC_API_KEY)"],
    ["<code>.\\run.ps1 serve --simulated</code>", "Web app with the scripted stand-in: no key, no cost"],
    ["<code>.\\run.ps1 cli --verbose</code>", "Terminal chat with agent activity and tokens"],
    ["<code>.\\run.ps1 test</code>", "Offline test suite: booking rules, states, guardrails, agents, harness, API, CLAUDE.md scans"],
    ["<code>.\\run.ps1 scenarios</code>", "Scenario suite, simulated; writes runs/&lt;id&gt;/"],
    ["<code>.\\run.ps1 scenarios --live --max-cost 5</code>", "Scenario suite with real Claude agents; cost cap checked between scenarios"],
    ["<code>.\\run.ps1 install-browser</code> then <code>.\\run.ps1 guide</code>", "Re-take the screenshots and rebuild this PDF"],
])}
<h3>Limits</h3>
<ul>
<li>Venues are fictional. Sales tax is the state base rate (approximate; local and meals taxes are not modeled), except the original New York and New Jersey venues, which use combined local rates.</li>
<li>The injection check catches known phrasings; the agents' instructions are the second line of defense.</li>
<li>Token budgets are checked before each call; two specialists running in parallel can overshoot by one call each.</li>
<li>Price grounding checks the Concierge's reply; arithmetic inside a specialist's reply is accepted as evidence.</li>
<li>Per-tool token numbers are attributions; the numbers in this guide are simulated estimates.</li>
<li>Sessions live in memory; there are no payments or accounts, and confirmation is checked by email match only.</li>
</ul>
</section>
</body></html>"""


def main():
    """Run the suite, take screenshots, write the HTML and print it to PDF.

    Side effects:
        Writes a ``runs/<id>-simulated/`` folder, ``docs/images/*.png``,
        ``docs/guide/how-it-works.html`` and ``docs/how-it-works.pdf``.
    """
    from playwright.sync_api import sync_playwright

    summary = run_suite("simulated")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        shots = take_screenshots(browser)
        out = write_text(HTML_OUT, build_html(summary, shots))
        page = browser.new_page()
        page.goto(out.as_uri())
        pdf = page.pdf(format="Letter", print_background=True, prefer_css_page_size=True)
        browser.close()
    from banquet.paths import write_bytes
    write_bytes(PDF_OUT, pdf)
    print(f"Wrote {confine(PDF_OUT)} ({len(pdf) // 1024} KB) and {out}")


if __name__ == "__main__":
    main()
