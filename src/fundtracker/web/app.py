"""The local site: the Launches and Emerging rankings behind a page, read-only.

create_app takes a connection factory, so tests pass a fixture database and
the server opens the real one read-only. Every page opens one connection
and closes it at the end of the request. Queries live in repo.py; this
module parses filters, calls repo and renders templates. Nothing here
writes to the database or stores per-user state; only the Refresh job
(jobs.py) writes, through ingest and derive.
"""

import io
import json
from datetime import date, datetime

from flask import Flask, abort, g, render_template, request, send_file, url_for

from fundtracker import classify, config, export, newness, outreach, traction
from fundtracker.db import repo
from fundtracker.web import filters, jobs, links

# Rows a page renders; the summary line says when there are more. The default
# Launches and Emerging views stay under it, so their exports match the screen.
DISPLAY_LIMIT = 1000


def create_app(connect=None, today=None, refresher=None):
    """The Flask app. connect() returns a new sqlite3 connection with sqlite3.Row
    rows (default: the configured database, read-only); today() returns the
    date the default filters count back from (default date.today); refresher
    is the jobs.Refresher behind the Refresh button (default: catchup then
    derive on the configured database)."""
    app = Flask(__name__)
    connect = connect or (lambda: repo.connect_readonly(config.DB_PATH))
    today = today or date.today
    refresher = refresher or jobs.Refresher(jobs.refresh_runner(config.DB_PATH))

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
        return render_template(template, status=repo.site_status(db()), job=refresher.snapshot(),
                               **context)

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

    def emerging_context():
        f = filters.emerging_filters(request.args, today())
        rows = emerging_rows(db(), f, today())
        return {"f": f, "rows": rows[:DISPLAY_LIMIT], "total": len(rows),
                "summary": filters.describe_emerging(f), "query": _query()}

    @app.get("/emerging")
    def emerging():
        return page("emerging.html", options=filters, **emerging_context())

    @app.get("/emerging/table")
    def emerging_table():
        return _partial("_emerging_table.html", "emerging", emerging_context())

    @app.get("/launches/export.xlsx")
    def launches_export():
        f = filters.launch_filters(request.args, today())
        rows = launch_rows(db(), f)
        meta = {"generated_at": datetime.now(), "since": f["since"].isoformat(), "type": f["type"],
                "min_score": f["min_score"], "last_daily": repo.last_ingest_key(db(), "daily"),
                "filters": filters.describe_launches(f)}
        out = io.BytesIO()
        export.write_workbook(out, rows, repo.funds_for_launches(db(), rows), meta)
        return _xlsx(out, f"launches-{f['type']}-{f['since']}-to-{f['until']}-{today():%Y%m%d}.xlsx")

    @app.get("/emerging/export.xlsx")
    def emerging_export():
        f = filters.emerging_filters(request.args, today())
        rows = emerging_rows(db(), f, today())
        oldest, newest = f["first_seen_from"], f["first_seen_to"]
        meta = {"generated_at": datetime.now(), "today": today(), "oldest": oldest, "newest": newest,
                "min_age": f"{(today() - newest).days}d", "max_age": f"{(today() - oldest).days}d",
                "type": f["type"], "min_score": f["min_score"], "max_funds": traction.EMERGING_MAX_FUNDS,
                "last_daily": repo.last_ingest_key(db(), "daily"), "filters": filters.describe_emerging(f)}
        funds = [dict(fund, manager_score=m["score"]) for m in rows for fund in m["funds"]]
        out = io.BytesIO()
        export.write_emerging_workbook(out, rows, funds, meta)
        return _xlsx(out, f"emerging-{f['type']}-{oldest}-to-{newest}-{today():%Y%m%d}.xlsx")

    def outreach_csv(manager_ids):
        managers = [repo.get_manager(db(), i) or abort(404) for i in manager_ids]
        out = io.StringIO()
        outreach.write_sheet_csv(out, outreach.sheet_rows(managers))
        return send_file(io.BytesIO(out.getvalue().encode("utf-8")), mimetype="text/csv",
                         as_attachment=True, download_name=f"outreach-{today():%Y%m%d}.csv")

    @app.post("/emerging/outreach.csv")
    def emerging_outreach():
        ids = list(dict.fromkeys(request.form.getlist("manager_id")))
        if not ids:
            abort(400, "Tick at least one manager.")
        return outreach_csv(ids)

    @app.get("/manager/<manager_id>/outreach.csv")
    def manager_outreach(manager_id):
        return outreach_csv([manager_id])

    @app.get("/launch/<launch_id>")
    def launch(launch_id):
        row = repo.get_launch(db(), launch_id) or abort(404)
        return page("launch.html", launch=row, reasons=_launch_reasons(row))

    @app.get("/manager/<manager_id>")
    def manager(manager_id):
        m = repo.get_manager(db(), manager_id) or abort(404)
        timeline = repo.filings_timeline(db(), manager_id)
        return page("manager.html", m=m, timeline=timeline, phone=repo.manager_phone(db(), m),
                    people=repo.people_on(db(), [t["accession"] for t in timeline]))

    @app.get("/person/<path:name_norm>")
    def person(name_norm):
        rows = repo.appearances(db(), name_norm)
        if not rows:
            abort(404)
        return page("person.html", name=repo.person_name(db(), name_norm) or name_norm,
                    name_norm=name_norm, rows=rows)

    @app.get("/search")
    def search():
        q = request.args.get("q", "").strip()
        return page("search.html", q=q, results=repo.search(db(), q) if q else None)

    @app.post("/refresh")
    def refresh():
        # Only the page's HTMX button may start a job: a cross-site form cannot set this header.
        if not request.headers.get("HX-Request"):
            abort(400)
        refresher.start()
        return refresh_status()

    @app.get("/refresh/status")
    def refresh_status():
        return render_template("_status.html", status=repo.site_status(db()), job=refresher.snapshot())

    @app.errorhandler(404)
    def not_found(_exc):
        return page("not_found.html"), 404

    return app


def launch_rows(conn, f):
    """Every launch the Launches filters f select, in the chosen order."""
    return repo.find_launches(
        conn, f["since"].isoformat(), f["until"].isoformat(), min_score=f["min_score"],
        fund_types=["Hedge Fund"] if f["type"] == "hedge" else None, rule=f["rule"],
        states=f["state"], min_inv=f["min_inv"], raised=f["raised"],
        hide_platforms=f["platforms"] == "hide", sort=f["sort"])


def emerging_rows(conn, f, today):
    """Every manager the Emerging filters f select, scored as of today, in the chosen order."""
    managers = repo.managers_first_seen(
        conn, f["first_seen_from"], f["first_seen_to"],
        ["Hedge Fund"] if f["type"] == "hedge" else None, exclude_over_cap=f["platforms"] == "hide",
        max_funds=traction.EMERGING_MAX_FUNDS, raised=f["raised"], states=f["state"],
        prior_firm=f["prior_firm"], adviser=f["adviser"], employees=f["employees"],
        outreach=f["outreach"])
    rows = traction.score_managers(managers, today, f["type"], f["min_score"])
    # Python's sort is stable, so each other order keeps the score order within a tie.
    if f["sort"] == "raised":
        rows.sort(key=lambda m: m["total_sold"] or 0, reverse=True)
    elif f["sort"] == "first_seen":
        rows.sort(key=lambda m: m["first_seen_date"], reverse=True)
    return rows


XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(buffer, filename):
    buffer.seek(0)
    return send_file(buffer, mimetype=XLSX_TYPE, as_attachment=True, download_name=filename)


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


def _people(manager):
    return [p["name"] for p in json.loads(manager["people"] or "[]")]


def _people_short(manager):
    """The first two names, then +N for the rest."""
    names = _people(manager)
    return ", ".join(names[:2]) + (f" +{len(names) - 2}" if len(names) > 2 else "")


def _register_filters(app):
    app.jinja_env.filters.update(
        amount=_amount,
        score_class=_score_class,
        launch_reasons=lambda r: "; ".join(newness.reason_phrase(x) for x in _launch_reasons(r)),
        platform=_platform,
        rule_506=lambda exemptions: classify.rule_506(json.loads(exemptions or "[]")),
        short_type=lambda t: (t or "").replace(" Fund", "").replace("Investment", "Inv"),
        n_funds=lambda row: len(json.loads(row["fund_ciks"] or "[]")),
        traction_reasons=lambda m: "; ".join(traction.reason_phrase(x) for x in json.loads(m["reasons"])),
        people_short=_people_short,
        prior_firms=lambda m: ", ".join(json.loads(m["prior_firms"] or "[]")),
        place=lambda m: ", ".join(p for p in (m["city"], m["state"] or m["country"]) if p),
        exemptions=lambda text: ", ".join(classify.sort_exemptions(json.loads(text or "[]"))),
        address=_address,
        adviser_label=_adviser_label,
        outreach=_outreach,
    )
    app.jinja_env.globals.update(links=links, reason_labels=newness.REASON_LABELS)


def _adviser_label(m):
    """RIA or ERA with the link's confidence: "RIA, high". Empty when unlinked."""
    kind = m.get("adviser_kind")
    return f"{kind.upper()}, {m['adviser_confidence']}" if kind else ""


def _outreach(m):
    """The outreach sheet's status and last action: "Outreach Sent, 2026-10-20". Empty when
    the manager is not in the sheet; "in the sheet" when its row has neither."""
    if not m.get("outreach_in_sheet"):
        return ""
    shown = [p for p in (m.get("outreach_status"), m.get("outreach_last_action")) if p]
    return ", ".join(shown) or "in the sheet"


def _address(f):
    """Street, city, state or country and zip as filed, one line."""
    place = f["state_or_country_desc"] or f["state_or_country"]
    return ", ".join(p for p in (f["street1"], f["street2"], f["city"], place, f["zip"]) if p)
