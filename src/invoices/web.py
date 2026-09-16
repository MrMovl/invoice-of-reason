"""HTTP routes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)

from . import archive, auth, backup, db
from .config import ConfigError, load_sender
from .pdf import FONT_DIR, FONT_FILES, LayoutOverflowError, format_amount, format_date

bp = Blueprint("web", __name__)

STATUS_LABELS = {"open": "Offen", "paid": "Bezahlt", "cancelled": "Storniert"}
EVENT_LABELS = {
    "created": "Erstellt",
    "imported": "Importiert",
    "notes": "Notiz geändert",
    "status:open": "Auf offen gesetzt",
    "status:paid": "Als bezahlt markiert",
    "status:cancelled": "Storniert",
}


def settings():
    return current_app.config["SETTINGS"]


def get_db():
    if "db" not in g:
        g.db = db.connect(settings().db_path)
    return g.db


@bp.app_template_filter("eur")
def eur_filter(cents: int) -> str:
    return format_amount(Decimal(cents) / 100)


@bp.app_template_filter("de_date")
def de_date_filter(iso: str | None) -> str:
    return format_date(date.fromisoformat(iso)) if iso else ""


@bp.app_template_filter("event_label")
def event_label_filter(action: str) -> str:
    return EVENT_LABELS.get(action, action)


@bp.app_context_processor
def inject_globals():
    return {
        "csrf_token": auth.csrf_token,
        "status_labels": STATUS_LABELS,
        "current_user": session.get("user"),
    }


@bp.before_app_request
def csrf_protect():
    auth.check_csrf()


@bp.after_app_request
def security_headers(resp: Response) -> Response:
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "frame-ancestors 'none'; form-action 'self'; base-uri 'none'",
    )
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if session.get("user"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@bp.get("/fonts/<name>")
def font(name: str):
    if name not in FONT_FILES.values():
        abort(404)
    return send_from_directory(FONT_DIR, name, mimetype="font/ttf", max_age=86400 * 30)


@bp.get("/healthz")
def healthz():
    get_db().execute("SELECT 1").fetchone()
    return {"status": "ok"}


# ── Auth ──────────────────────────────────────────────────────────────────


def _safe_next(target: str | None) -> str:
    if target:
        parsed = urlparse(target)
        if not parsed.scheme and not parsed.netloc and target.startswith("/") and not target.startswith("//"):
            return target
    return url_for("web.invoice_list")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        key = auth.client_key()
        if auth.throttle.locked(key):
            flash("Zu viele Fehlversuche. Bitte 15 Minuten warten.", "error")
            return render_template("login.html"), 429
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if auth.check_credentials(username, password):
            auth.throttle.reset(key)
            auth.login_user(username)
            return redirect(_safe_next(request.args.get("next")))
        auth.throttle.fail(key)
        flash("Benutzername oder Passwort falsch.", "error")
        return render_template("login.html"), 401
    return render_template("login.html")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("web.login"))


# ── Invoices ──────────────────────────────────────────────────────────────


@bp.get("/")
@auth.login_required
def index():
    return redirect(url_for("web.invoice_list"))


@bp.get("/invoices")
@auth.login_required
def invoice_list():
    conn = get_db()
    years = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(issue_date, 1, 4) FROM invoices ORDER BY 1 DESC")]
    year = request.args.get("year", "")
    status = request.args.get("status", "")
    q = request.args.get("q", "").strip()

    where, params = [], []
    if year:
        where.append("substr(issue_date, 1, 4) = ?")
        params.append(year)
    if status in STATUS_LABELS:
        where.append("status = ?")
        params.append(status)
    if q:
        where.append("(number LIKE ? OR customer_name LIKE ? OR title LIKE ?)")
        params += [f"%{q}%"] * 3
    sql = "SELECT * FROM invoices"
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = conn.execute(sql + " ORDER BY issue_date DESC, number DESC", params).fetchall()

    today = date.today().isoformat()
    totals = {
        "count": len(rows),
        "billed": sum(r["amount_cents"] for r in rows if r["status"] != "cancelled"),
        "paid": sum(r["amount_cents"] for r in rows if r["status"] == "paid"),
        "open": sum(r["amount_cents"] for r in rows if r["status"] == "open"),
        "overdue": sum(1 for r in rows if r["status"] == "open" and r["due_date"] and r["due_date"] < today),
    }
    return render_template("list.html", rows=rows, years=years, year=year, status=status,
                           q=q, totals=totals, today=today)


def _customers(conn):
    """Most recent address per customer, for autofill."""
    return conn.execute(
        """SELECT customer_name, customer_street, customer_city FROM invoices
           WHERE id IN (SELECT MAX(id) FROM invoices GROUP BY customer_name)
           ORDER BY customer_name"""
    ).fetchall()


def _new_form_defaults(conn) -> dict:
    today = date.today()
    form = {
        "number": archive.next_number(conn, today.year),
        "issue_date": today.isoformat(),
        "service_from": today.isoformat(),
        "service_to": "",
        "payment_days": "14",
    }
    copy_id = request.args.get("from", type=int)
    if copy_id:
        src = conn.execute("SELECT * FROM invoices WHERE id = ?", (copy_id,)).fetchone()
        if src:
            for key in ("customer_name", "customer_street", "customer_city", "title", "description"):
                form[key] = src[key]
    return form


def _load_sender():
    try:
        return load_sender(settings().sender_file)
    except ConfigError as e:
        abort(500, str(e))


@bp.get("/invoices/new")
@auth.login_required
def invoice_new():
    conn = get_db()
    _load_sender()  # fail early if the sender config is missing
    return render_template("new.html", form=_new_form_defaults(conn), customers=_customers(conn))


@bp.post("/invoices/preview")
@auth.login_required
def invoice_preview():
    from .pdf import render_invoice

    try:
        inp = archive.parse_invoice_form(request.form)
        pdf = render_invoice(inp.to_invoice_data(), _load_sender())
    except (archive.ArchiveError, LayoutOverflowError) as e:
        return Response(f"Vorschau nicht möglich: {e}", status=400, mimetype="text/plain")
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="Vorschau.pdf"'})


@bp.post("/invoices")
@auth.login_required
def invoice_create():
    conn = get_db()
    s = settings()
    try:
        inp = archive.parse_invoice_form(request.form)
        invoice_id = archive.issue_invoice(conn, s.archive_dir, inp, _load_sender(), s.retention_years)
    except (archive.ArchiveError, LayoutOverflowError) as e:
        flash(str(e), "error")
        return render_template("new.html", form=request.form, customers=_customers(conn)), 400
    flash("Rechnung erstellt und archiviert.", "ok")
    return redirect(url_for("web.invoice_detail", invoice_id=invoice_id))


def _get_invoice(invoice_id: int):
    row = get_db().execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if row is None:
        abort(404)
    return row


@bp.get("/invoices/<int:invoice_id>")
@auth.login_required
def invoice_detail(invoice_id: int):
    row = _get_invoice(invoice_id)
    events = get_db().execute(
        "SELECT * FROM events WHERE invoice_id = ? ORDER BY id DESC", (invoice_id,)).fetchall()
    problem = archive.verify_invoice(settings().archive_dir, row)
    return render_template("detail.html", inv=row, events=events, problem=problem,
                           today=date.today().isoformat())


@bp.get("/invoices/<int:invoice_id>/pdf")
@auth.login_required
def invoice_pdf(invoice_id: int):
    row = _get_invoice(invoice_id)
    path = settings().archive_dir / row["pdf_path"]
    if not path.is_file():
        abort(404, "PDF fehlt im Archiv.")
    return send_file(path, mimetype="application/pdf", as_attachment=not request.args.get("inline"),
                     download_name=Path(row["pdf_path"]).name)


@bp.post("/invoices/<int:invoice_id>/status")
@auth.login_required
def invoice_status(invoice_id: int):
    _get_invoice(invoice_id)
    status = request.form.get("status", "")
    try:
        paid = None
        if status == "paid":
            paid = archive.parse_date(request.form.get("paid_date"), "Zahlungsdatum")
        archive.set_status(get_db(), invoice_id, status, paid, request.form.get("note", ""))
        flash(f"Status: {STATUS_LABELS[status]}.", "ok")
    except archive.ArchiveError as e:
        flash(str(e), "error")
    return redirect(url_for("web.invoice_detail", invoice_id=invoice_id))


@bp.post("/invoices/<int:invoice_id>/notes")
@auth.login_required
def invoice_notes(invoice_id: int):
    _get_invoice(invoice_id)
    archive.set_notes(get_db(), invoice_id, request.form.get("notes", ""))
    flash("Notiz gespeichert.", "ok")
    return redirect(url_for("web.invoice_detail", invoice_id=invoice_id))


@bp.route("/invoices/import", methods=["GET", "POST"])
@auth.login_required
def invoice_import():
    conn = get_db()
    if request.method == "POST":
        upload = request.files.get("pdf")
        try:
            if not upload or not upload.filename:
                raise archive.ArchiveError("Bitte eine PDF-Datei auswählen.")
            form = request.form.to_dict()
            form["original_filename"] = upload.filename
            invoice_id = archive.import_invoice(conn, settings().archive_dir, form, upload.read(),
                                                settings().retention_years)
        except archive.ArchiveError as e:
            flash(str(e), "error")
            return render_template("import.html", form=request.form, customers=_customers(conn)), 400
        flash("Rechnung importiert und archiviert.", "ok")
        return redirect(url_for("web.invoice_detail", invoice_id=invoice_id))
    return render_template("import.html", form={"issue_date": date.today().isoformat()},
                           customers=_customers(conn))


# ── Backups ───────────────────────────────────────────────────────────────


@bp.get("/backups")
@auth.login_required
def backup_list():
    conn = get_db()
    problems = archive.verify_all(conn, settings().archive_dir)
    count = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    return render_template("backups.html", backups=backup.list_backups(settings()),
                           problems=problems, count=count)


@bp.post("/backups")
@auth.login_required
def backup_create():
    try:
        path = backup.create_backup(settings())
        flash(f"Backup erstellt: {path.name}", "ok")
    except backup.BackupError as e:
        flash(str(e), "error")
    return redirect(url_for("web.backup_list"))


@bp.get("/backups/<name>")
@auth.login_required
def backup_download(name: str):
    if not backup.BACKUP_RE.match(name):
        abort(404)
    path = settings().backup_dir / name
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="application/gzip", as_attachment=True, download_name=name)


@bp.app_errorhandler(400)
@bp.app_errorhandler(404)
@bp.app_errorhandler(413)
@bp.app_errorhandler(500)
def error_page(e):
    code = getattr(e, "code", 500)
    message = getattr(e, "description", "Interner Fehler")
    if code == 413:
        message = "Datei zu groß (max. 20 MB)."
    return render_template("error.html", code=code, message=message), code
