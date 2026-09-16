import re

import pytest
from werkzeug.security import generate_password_hash

EXAMPLE_SENDER = "config/sender.example.toml"
PASSWORD = "correct horse battery"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("INVOICES_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INVOICES_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("INVOICES_SENDER_FILE", EXAMPLE_SENDER)
    monkeypatch.setenv("INVOICES_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("INVOICES_USERNAME", "testuser")
    monkeypatch.setenv("INVOICES_PASSWORD_HASH", generate_password_hash(PASSWORD))
    monkeypatch.setenv("INVOICES_INSECURE_COOKIES", "")
    return tmp_path


@pytest.fixture
def app(env):
    from invoices import auth, create_app

    auth.throttle = auth.LoginThrottle()
    app = create_app({"TESTING": True})
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def csrf_from(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


@pytest.fixture
def logged_in(client):
    page = client.get("/login").get_data(as_text=True)
    resp = client.post("/login", data={"username": "testuser", "password": PASSWORD, "csrf_token": csrf_from(page)})
    assert resp.status_code == 302
    return client


@pytest.fixture
def csrf(logged_in):
    return csrf_from(logged_in.get("/invoices/new").get_data(as_text=True))


def invoice_form(**overrides):
    form = {
        "number": "2026-001",
        "issue_date": "2026-09-16",
        "service_from": "2026-09-16",
        "service_to": "",
        "payment_days": "14",
        "customer_name": "Nordlicht Werkstatt GmbH",
        "customer_street": "Hafenstraße 7",
        "customer_city": "24103 Kiel",
        "title": "Entwicklung & Beratung <intern>",
        "description": "Konzeption und Umsetzung.",
        "amount": "700,00",
    }
    form.update(overrides)
    return form
