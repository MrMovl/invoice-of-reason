from tests.conftest import PASSWORD, csrf_from, invoice_form


def test_requires_login(client):
    assert client.get("/invoices").status_code == 302
    assert client.get("/invoices/1/pdf").headers["Location"].startswith("/login")
    assert client.get("/healthz").status_code == 200


def test_login_rejects_bad_password_and_throttles(client):
    csrf = csrf_from(client.get("/login").get_data(as_text=True))
    for _ in range(5):
        assert client.post("/login", data={"username": "testuser", "password": "nope", "csrf_token": csrf}).status_code == 401
    resp = client.post("/login", data={"username": "testuser", "password": PASSWORD, "csrf_token": csrf})
    assert resp.status_code == 429


def test_login_rejects_missing_csrf(client):
    client.get("/login")
    assert client.post("/login", data={"username": "testuser", "password": PASSWORD}).status_code == 400


def test_open_redirect_blocked(client):
    csrf = csrf_from(client.get("/login").get_data(as_text=True))
    resp = client.post("/login?next=//evil.example", data={"username": "testuser", "password": PASSWORD, "csrf_token": csrf})
    assert resp.headers["Location"] == "/invoices"
    client.post("/logout", data={"csrf_token": csrf_from(client.get("/invoices", follow_redirects=True).get_data(as_text=True))})
    csrf = csrf_from(client.get("/login").get_data(as_text=True))
    resp = client.post("/login?next=/\\evil.example", data={"username": "testuser", "password": PASSWORD, "csrf_token": csrf})
    assert resp.headers["Location"] == "/invoices"


def test_create_preview_download_and_pay(logged_in, csrf):
    c = logged_in
    preview = c.post("/invoices/preview", data={**invoice_form(), "csrf_token": csrf})
    assert preview.status_code == 200 and preview.data.startswith(b"%PDF-")
    assert "2026-001" not in c.get("/invoices").get_data(as_text=True)

    resp = c.post("/invoices", data={**invoice_form(), "csrf_token": csrf})
    assert resp.status_code == 302
    detail_url = resp.headers["Location"]
    html = c.get(detail_url).get_data(as_text=True)
    assert "Rechnung 2026-001" in html and "geprüft" in html
    assert "&lt;intern&gt;" in html

    pdf = c.get(detail_url + "/pdf")
    assert pdf.data.startswith(b"%PDF-")
    assert "Rechnung_2026-001_Nordlicht-Werkstatt-GmbH.pdf" in pdf.headers["Content-Disposition"]

    c.post(detail_url + "/status", data={"status": "paid", "paid_date": "2026-09-20", "csrf_token": csrf})
    assert "Bezahlt" in c.get(detail_url).get_data(as_text=True)
    listing = c.get("/invoices?year=2026").get_data(as_text=True)
    assert "700,00 €" in listing

    dup = c.post("/invoices", data={**invoice_form(), "csrf_token": csrf})
    assert dup.status_code == 400 and "bereits vergeben" in dup.get_data(as_text=True)


def test_backup_page(logged_in, csrf):
    c = logged_in
    c.post("/invoices", data={**invoice_form(), "csrf_token": csrf})
    assert c.post("/backups", data={"csrf_token": csrf}).status_code == 302
    page = c.get("/backups").get_data(as_text=True)
    name = page.split("invoices-backup-")[1].split(".tar.gz")[0]
    dl = c.get(f"/backups/invoices-backup-{name}.tar.gz")
    assert dl.status_code == 200 and dl.data[:2] == b"\x1f\x8b"
    assert c.get("/backups/..%2finvoices.sqlite3").status_code == 404
    assert c.get("/invoices/import").status_code == 404


def test_security_headers(logged_in):
    resp = logged_in.get("/invoices")
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["Strict-Transport-Security"] == "max-age=31536000"


def test_expense_upload_review_and_overview(logged_in, csrf):
    import io
    import shutil

    from tests.conftest import SAMPLE_EXPENSE, make_pdf

    c = logged_in
    pdf = make_pdf(SAMPLE_EXPENSE)
    resp = c.post("/expenses/upload", content_type="multipart/form-data", data={
        "csrf_token": csrf, "files": [(io.BytesIO(pdf), "hetzner.pdf")]})
    assert resp.status_code == 302
    detail_url = resp.headers["Location"]
    html = c.get(detail_url).get_data(as_text=True)
    assert "Geprüft, speichern" in html
    if shutil.which("pdftotext"):
        assert 'value="11,90"' in html and "Hetzner Online GmbH" in html

    resp = c.post("/expenses/upload", content_type="multipart/form-data", data={
        "csrf_token": csrf, "files": [(io.BytesIO(pdf), "again.pdf"),
                                      (io.BytesIO(b"GIF89a"), "x.gif")]})
    flashes = c.get(resp.headers["Location"]).get_data(as_text=True)
    assert "bereits hochgeladen" in flashes and "Nur PDF" in flashes

    bad = c.post(detail_url, data={"csrf_token": csrf, "status": "paid", "vendor": "Hetzner", "amount": "",
                                   "expense_date": "2026-09-03"})
    assert bad.status_code == 400 and "Betrag fehlt" in bad.get_data(as_text=True)
    ok = c.post(detail_url, data={"csrf_token": csrf, "status": "paid", "vendor": "Hetzner Online GmbH",
                                  "amount": "11,90", "expense_date": "2026-09-03", "category": "Hosting"})
    assert ok.status_code == 302
    assert "badge-overdue\">Zu prüfen" not in c.get("/expenses").get_data(as_text=True)
    assert "Hosting" in c.get("/expenses?q=Cloud+Server").get_data(as_text=True)

    doc = c.get(detail_url + "/file?inline=1")
    assert doc.data.startswith(b"%PDF-") and doc.mimetype == "application/pdf"
    assert c.get("/expenses/999").status_code == 404

    overview = c.get("/invoices?year=2026").get_data(as_text=True)
    assert "Einnahmen und Ausgaben 2026" in overview and "-11,90 €" in overview
