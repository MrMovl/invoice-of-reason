"""HTTP routes."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from functools import lru_cache
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
    session,
    url_for,
)

from . import archive, auth, backup, chain, db, einvoice, expenses, export, system, turnover
from .config import ConfigError, load_sender
from .pdf import LayoutOverflowError, format_amount, format_date

bp = Blueprint("web", __name__)

STATUS_LABELS = {"open": "Offen", "paid": "Bezahlt", "cancelled": "Storniert"}
SUGGESTION_SOURCES = {
    "xml": "E-Rechnung (XML)",
    "zugferd": "ZUGFeRD/Factur-X (XML im PDF)",
    "text": "PDF-Text",
    "none": "–",
}
EXPENSE_STATUS_LABELS = {"paid": "Bezahlt", "open": "Offen", "void": "Verworfen"}
CONTROL_LABELS = {
    "verify": "Integritätsprüfung",
    "backup": "Backup",
    "restore_test": "Wiederherstellungstest",
    "export": "Datenexport",
}
EVENT_LABELS = {
    "created": "Erstellt",
    "uploaded": "Hochgeladen",
    "reviewed": "Geprüft",
    "updated": "Geändert",
    "imported": "Importiert",
    "notes": "Notiz geändert",
    "status:open": "Auf offen gesetzt",
    "status:paid": "Als bezahlt markiert",
    "status:cancelled": "Storniert",
    "sealed": "Versiegelt",
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


@bp.app_template_filter("money")
def money_filter(amount: Decimal | None, currency: str = "EUR") -> str:
    """E-invoice amounts: euro as usual, other currencies with their ISO code."""
    if amount is None:
        return "–"
    text = format_amount(amount)
    return text if currency in ("EUR", "") else f"{text[:-2]} {currency}"


@bp.app_template_filter("de_num")
def de_num_filter(value: Decimal | None) -> str:
    return f"{value.normalize():f}".replace(".", ",") if value is not None else ""


@bp.app_template_filter("event_label")
def event_label_filter(action: str) -> str:
    return EVENT_LABELS.get(action, action)


@lru_cache(maxsize=64)
def _static_version(folder: str, filename: str) -> str:
    try:
        return hashlib.sha256((Path(folder) / filename).read_bytes()).hexdigest()[:10]
    except OSError:
        return ""


@bp.app_url_defaults
def static_cache_busting(endpoint, values):
    # Cloudflare sets a 4 h browser cache on static files. A content hash in the URL makes
    # every deploy fetch changed CSS/JS immediately instead of mixing new HTML with old CSS.
    # Fonts stay unversioned: style.css loads them by plain URL, and preloads must match it.
    if endpoint == "static" and "filename" in values and "v" not in values \
            and not values["filename"].startswith("fonts/"):
        version = _static_version(current_app.static_folder, values["filename"])
        if version:
            values["v"] = version


@bp.app_context_processor
def inject_globals():
    return {
        "csrf_token": auth.csrf_token,
        "status_labels": STATUS_LABELS,
        "expense_status_labels": EXPENSE_STATUS_LABELS,
        "payment_labels": archive.PAYMENT_METHODS,
        "current_user": session.get("user"),
        "app_version": system.app_version(),
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
    if current_app.config["SESSION_COOKIE_SECURE"]:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if session.get("user"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@bp.get("/healthz")
def healthz():
    get_db().execute("SELECT 1").fetchone()
    return {"status": "ok"}


# ── Auth ──────────────────────────────────────────────────────────────────


def _safe_next(target: str | None) -> str:
    if target:
        parsed = urlparse(target)
        if (not parsed.scheme and not parsed.netloc and target.startswith("/")
                and not target.startswith("//") and "\\" not in target):
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
    return render_template("list.html", turnover=_turnover(conn), rows=rows, years=years, year=year, status=status,
                           q=q, totals=totals, today=today,
                           cash=expenses.cash_summary(conn, year),
                           number_findings=archive.number_findings(conn),
                           receipt_findings=archive.lost_receipt_findings(conn))


def _turnover(conn) -> turnover.Status:
    return turnover.status(conn, settings().founding_year)


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
    return render_template("new.html", turnover=_turnover(conn), form=_new_form_defaults(conn), customers=_customers(conn))


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
        # sender.toml can change without a restart: log it before it shapes a new invoice.
        system.record_config(conn, s)
        invoice_id = archive.issue_invoice(conn, s.archive_dir, inp, _load_sender(), s.retention_years,
                                           founding_year=s.founding_year)
    except (archive.ArchiveError, LayoutOverflowError) as e:
        flash(str(e), "error")
        return render_template("new.html", turnover=_turnover(conn), form=request.form, customers=_customers(conn)), 400
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
        archive.set_status(get_db(), invoice_id, status, paid, request.form.get("note", ""),
                           request.form.get("payment_method", ""))
        flash(f"Status: {STATUS_LABELS[status]}.", "ok")
    except archive.ArchiveError as e:
        flash(str(e), "error")
    return redirect(url_for("web.invoice_detail", invoice_id=invoice_id))


@bp.post("/invoices/<int:invoice_id>/notes")
@auth.login_required
def invoice_notes(invoice_id: int):
    _get_invoice(invoice_id)
    try:
        archive.set_notes(get_db(), invoice_id, request.form.get("notes", ""))
        flash("Notiz gespeichert.", "ok")
    except archive.ArchiveError as e:
        flash(str(e), "error")
    return redirect(url_for("web.invoice_detail", invoice_id=invoice_id))


# ── Expenses ──────────────────────────────────────────────────────────────


@bp.get("/expenses")
@auth.login_required
def expense_list():
    conn = get_db()
    booked = expenses.booking_date_sql()
    years = [r[0] for r in conn.execute(
        f"SELECT DISTINCT substr({booked}, 1, 4) FROM expenses ORDER BY 1 DESC")]
    year = request.args.get("year", "")
    status = request.args.get("status", "")
    category = request.args.get("category", "")
    review = request.args.get("review", "")
    rc = request.args.get("rc", "")
    asset = request.args.get("asset", "")
    q = request.args.get("q", "").strip()

    where, params = [], []
    if asset:
        where.append("treatment = ?")
        params.append(expenses.ASSET)
    if rc:
        where.append("reverse_charge = ?")
        params.append(expenses.REVERSE_CHARGE)
    if year:
        where.append(f"substr({booked}, 1, 4) = ?")
        params.append(year)
    if status in EXPENSE_STATUS_LABELS:
        where.append("status = ?")
        params.append(status)
    if category:
        where.append("category = ?")
        params.append(category)
    if review:
        where.append("reviewed = 0 AND status != 'void'")
    if q:
        where.append("(vendor LIKE ? OR invoice_number LIKE ? OR category LIKE ? OR notes LIKE ?"
                     " OR original_filename LIKE ? OR doc_text LIKE ?)")
        params += [f"%{q}%"] * 6
    sql = f"SELECT *, {booked} AS booked_on FROM expenses"
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = conn.execute(sql + " ORDER BY booked_on DESC, id DESC", params).fetchall()

    totals = {
        "count": len(rows),
        "paid": sum(r["amount_cents"] or 0 for r in rows if r["status"] == "paid"),
        "open": sum(r["amount_cents"] or 0 for r in rows if r["status"] == "open"),
        "to_review": sum(1 for r in rows if not r["reviewed"] and r["status"] != "void"),
    }
    late = {r["id"] for r in rows if expenses.review_overdue(r)}
    return render_template("expenses.html", rows=rows, years=years, year=year, status=status,
                           category=category, categories=_categories(conn), review=review,
                           q=q, totals=totals, late=late, review_days=expenses.REVIEW_DAYS, rc=rc, asset=asset,
                           rc_summary=expenses.reverse_charge_summary(
                               conn, int(year) if re.fullmatch(r"\d{4}", year) else date.today().year))


def _categories(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT category FROM expenses WHERE category != '' ORDER BY category")]


@bp.post("/expenses/upload")
@auth.login_required
def expense_upload():
    conn = get_db()
    s = settings()
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("Keine Datei ausgewählt.", "error")
        return redirect(url_for("web.expense_list"))
    created, errors = [], []
    for f in files:
        try:
            # Read one byte past the limit so oversized files are rejected without loading them whole.
            data = f.read(expenses.MAX_FILE_SIZE + 1)
            created.append(expenses.store_upload(conn, s.expenses_dir, data, f.filename,
                                                 s.retention_years))
        except archive.ArchiveError as e:
            errors.append(f"{f.filename}: {e}")
    for message in errors:
        flash(message, "error")
    if len(files) == 1 and created:
        flash("Beleg hochgeladen. Erkannte Werte prüfen und speichern.", "ok")
        return redirect(url_for("web.expense_detail", expense_id=created[0]))
    if created:
        flash(f"{len(created)} Belege hochgeladen. Bitte die erkannten Werte prüfen.", "ok")
    return redirect(url_for("web.expense_list", review=1 if created else None))


def _hint_values(form) -> dict:
    """Stored row or a re-shown form after a failed save: both need amount_cents for the hints."""
    values = dict(form)
    if "amount_cents" not in values:
        try:
            values["amount_cents"] = int(archive.parse_amount(form.get("amount") or "") * 100)
        except archive.ArchiveError:
            values["amount_cents"] = None
    return values


def _get_expense(expense_id: int):
    row = get_db().execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if row is None:
        abort(404)
    return row


@bp.get("/expenses/<int:expense_id>")
@auth.login_required
def expense_detail(expense_id: int, form=None):
    conn = get_db()
    row = _get_expense(expense_id)
    events = conn.execute(
        "SELECT * FROM expense_events WHERE expense_id = ? ORDER BY id DESC", (expense_id,)).fetchall()
    if form is None:
        form = dict(row)
        form["amount"] = format_amount(Decimal(row["amount_cents"]) / 100)[:-2] if row["amount_cents"] else ""
    problem = expenses.verify_expense(settings().expenses_dir, row)
    next_review = conn.execute(
        "SELECT id FROM expenses WHERE reviewed = 0 AND status != 'void' AND id != ? ORDER BY id LIMIT 1",
        (expense_id,)).fetchone()
    suggestion = json.loads(row["suggestion_json"])
    suggestion["amount_cents"] = int(Decimal(suggestion["amount"]) * 100) if suggestion["amount"] else None
    # Uploads before e-invoice support recorded no source.
    suggestion.setdefault("source", "text" if suggestion.get("text_layer") else "none")
    invoice, invoice_error = None, None
    if row["doc_type"] == "xml":
        # Readable view (GoBD Rz. 156): rendered from the archived XML itself, not from stored copies.
        try:
            invoice = einvoice.parse((settings().expenses_dir / row["doc_path"]).read_bytes())
        except (OSError, einvoice.EInvoiceError) as e:
            invoice_error = str(e) if isinstance(e, einvoice.EInvoiceError) else "Datei fehlt im Archiv."
    return render_template("expense.html", exp=row, form=form, events=events, problem=problem,
                           asset_hint=expenses.asset_hint(_hint_values(form)),
                           gwg_limit=expenses.GWG_LIMIT_NET_CENTS,
                           suggestion=suggestion, suggestion_sources=SUGGESTION_SOURCES,
                           invoice=invoice, invoice_error=invoice_error,
                           review_late=expenses.review_overdue(row), review_days=expenses.REVIEW_DAYS,
                           categories=_categories(conn), next_review=next_review,
                           today=date.today().isoformat())


@bp.post("/expenses/<int:expense_id>")
@auth.login_required
def expense_update(expense_id: int):
    _get_expense(expense_id)
    try:
        values = expenses.parse_expense_form(request.form)
        expenses.update_expense(get_db(), expense_id, values)
    except archive.ArchiveError as e:
        flash(str(e), "error")
        return expense_detail(expense_id, form=request.form), 400
    flash("Beleg gespeichert.", "ok")
    if request.form.get("next") == "review":
        nxt = get_db().execute(
            "SELECT id FROM expenses WHERE reviewed = 0 AND status != 'void' ORDER BY id LIMIT 1").fetchone()
        if nxt:
            return redirect(url_for("web.expense_detail", expense_id=nxt["id"]))
    return redirect(url_for("web.expense_detail", expense_id=expense_id))


@bp.get("/expenses/<int:expense_id>/file")
@auth.login_required
def expense_file(expense_id: int):
    row = _get_expense(expense_id)
    path = settings().expenses_dir / row["doc_path"]
    if not path.is_file():
        abort(404, "Datei fehlt im Archiv.")
    inline = bool(request.args.get("inline"))
    if inline and row["doc_type"] == "xml":
        # Shown as source text: a browser must never render uploaded XML (XSLT, XHTML scripts).
        resp = send_file(path, mimetype="text/plain", as_attachment=False,  # werkzeug adds charset=utf-8
                         download_name=Path(row["doc_path"]).name)
        resp.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
        return resp
    return send_file(path, mimetype=expenses.mimetype(row["doc_type"]),
                     as_attachment=not inline, download_name=Path(row["doc_path"]).name)


# ── Backups ───────────────────────────────────────────────────────────────


@bp.get("/backups")
@auth.login_required
def backup_list():
    conn = get_db()
    problems = archive.verify_all(conn, settings().archive_dir) \
        + expenses.verify_all(conn, settings().expenses_dir) + chain.verify_chains(conn)
    count = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    expense_count = conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
    runs = conn.execute("SELECT * FROM control_runs ORDER BY id DESC LIMIT 20").fetchall()
    return render_template("backups.html", backups=backup.list_backups(settings()),
                           problems=problems, count=count, expense_count=expense_count, runs=runs,
                           control_labels=CONTROL_LABELS, exports=export.list_exports(settings()),
                           export_years=export.export_years(conn))


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


@bp.post("/backups/exports")
@auth.login_required
def export_create():
    try:
        path = export.create_export(settings(), request.form.get("year") or None)
        flash(f"Datenexport erstellt: {path.name}", "ok")
    except export.ExportError as e:
        flash(str(e), "error")
    return redirect(url_for("web.backup_list"))


@bp.get("/backups/exports/<name>")
@auth.login_required
def export_download(name: str):
    if not export.EXPORT_RE.match(name):
        abort(404)
    path = settings().backup_dir / "exports" / name
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="application/zip", as_attachment=True, download_name=name)


@bp.app_errorhandler(400)
@bp.app_errorhandler(404)
@bp.app_errorhandler(413)
@bp.app_errorhandler(500)
def error_page(e):
    code = getattr(e, "code", 500)
    message = getattr(e, "description", "Interner Fehler")
    if code == 413:
        message = "Upload zu groß (max. 50 MB)."
    return render_template("error.html", code=code, message=message), code
