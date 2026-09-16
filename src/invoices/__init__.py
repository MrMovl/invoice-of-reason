"""invoice-of-reason: create, archive and back up invoices, and track expenses."""

from __future__ import annotations

import os
from datetime import timedelta

from flask import Flask, g

from . import db
from .config import load_settings


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    settings = load_settings()
    app.config.update(
        SETTINGS=settings,
        SECRET_KEY=os.environ.get("INVOICES_SECRET_KEY", ""),
        AUTH_USERNAME=os.environ.get("INVOICES_USERNAME", ""),
        AUTH_PASSWORD_HASH=os.environ.get("INVOICES_PASSWORD_HASH", ""),
        SESSION_COOKIE_NAME="invoices_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("INVOICES_INSECURE_COOKIES", "") != "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,
    )
    if overrides:
        app.config.update(overrides)

    for key in ("SECRET_KEY", "AUTH_USERNAME", "AUTH_PASSWORD_HASH"):
        if not app.config[key]:
            raise RuntimeError(
                f"{key} ist nicht gesetzt (INVOICES_SECRET_KEY, INVOICES_USERNAME, "
                "INVOICES_PASSWORD_HASH). Siehe .env.example."
            )
    if len(app.config["SECRET_KEY"]) < 32:
        raise RuntimeError("INVOICES_SECRET_KEY muss mindestens 32 Zeichen lang sein.")

    settings = app.config["SETTINGS"]
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    conn.close()
    settings.archive_dir.mkdir(parents=True, exist_ok=True)
    settings.expenses_dir.mkdir(parents=True, exist_ok=True)

    @app.teardown_appcontext
    def close_db(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    from .web import bp

    app.register_blueprint(bp)
    return app
