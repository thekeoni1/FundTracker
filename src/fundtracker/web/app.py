"""The local site: the Launches and Emerging rankings behind a page, read-only.

create_app takes a connection factory, so tests pass a fixture database and
the server opens the real one read-only. Every page opens one connection
and closes it at the end of the request. Queries live in repo.py; this
module parses filters, calls repo and renders templates. Nothing here
writes to the database or stores per-user state.
"""

import json
from datetime import date

from flask import Flask, g, render_template, request, url_for

from fundtracker import classify, config, newness
from fundtracker.db import repo
from fundtracker.web import filters, links

# Rows a page renders; the summary line says when there are more.
DISPLAY_LIMIT = 500


def create_app(connect=None, today=None):
    """The Flask app. connect() returns a new sqlite3 connection with sqlite3.Row
    rows (default: the configured database, read-only); today() returns the
    date the default filters count back from (default date.today)."""
    app = Flask(__name__)
    connect = connect or (lambda: repo.connect_readonly(config.DB_PATH))
    today = today or date.today

    def db():
        if "conn" not in g:
            g.conn = connect()
        return g.conn

    @app.teardown_appcontext
    def close_db(_exc):
        conn = g.pop("conn", None)
        if conn is not None:
            conn.close()

    def page(template, **context):
        return render_template(template, status=repo.site_status(db()), **context)

    _register_filters(app)

    @app.get("/")
    def index():
        return app.redirect(url_for("launches"))

    def launch_context():
        f = filters.launch_filters(request.args, today())
        rows = launch_rows(db(), f)
        return {"f": f, "rows": rows[:DISPLAY_LIMIT], "total": len(rows),
                "summary": filters.describe_launches(f), "query": _query()}

    @app.get("/launches")
    def launches():
        return page("launches.html", options=filters, **launch_context())

    @app.get("/launches/table")
    def launches_table():
        return _partial("_launches_table.html", "launches", launch_context())

    return app


def launch_rows(conn, f):
    """Every launch the Launches filters f select, in the chosen order."""
    return repo.find_launches(
        conn, f["since"].isoformat(), f["until"].isoformat(), min_score=f["min_score"],
        fund_types=["Hedge Fund"] if f["type"] == "hedge" else None, rule=f["rule"],
        states=f["state"], min_inv=f["min_inv"], raised=f["raised"],
        hide_platforms=f["platforms"] == "hide", sort=f["sort"])


def _query():
    """The request's query string, for the export link and the pushed page URL."""
    return request.query_string.decode()


def _partial(template, page_endpoint, context):
    """A table partial. Asked for by HTMX, it moves the address bar to the page's own
    URL with the same filters, so the view stays bookmarkable."""
    response = render_template(template, **context)
    headers = {}
    if request.headers.get("HX-Request"):
        query = context["query"]
        headers["HX-Push-Url"] = url_for(page_endpoint) + (f"?{query}" if query else "")
    return response, 200, headers


def _amount(value):
    return "" if value is None else f"{value:,}"


def _score_class(score):
    score = score or 0
    return "high" if score >= 80 else "mid" if score >= 50 else "low"


def _launch_reasons(row):
    return json.loads(row["newness_reasons"] or "[]")


def _platform(row):
    return next((r["detail"] for r in _launch_reasons(row) if r["code"] == "platform"), "")


def _register_filters(app):
    app.jinja_env.filters.update(
        amount=_amount,
        score_class=_score_class,
        launch_reasons=lambda r: "; ".join(newness.reason_phrase(x) for x in _launch_reasons(r)),
        platform=_platform,
        rule_506=lambda exemptions: classify.rule_506(json.loads(exemptions or "[]")),
        short_type=lambda t: (t or "").replace(" Fund", "").replace("Investment", "Inv"),
        n_funds=lambda row: len(json.loads(row["fund_ciks"] or "[]")),
    )
    app.jinja_env.globals.update(links=links)
