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
