"""Programmidentität and configuration history (GoBD Rz. 111, 154).

The deployed version is baked into the image (INVOICES_VERSION, set by deploy.sh from the git
commit). Every change of version or of the settings that shape invoices and retention is
recorded in the append-only system_events table, so it is provable which program and which
configuration were in use at any time.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict

from . import db
from .config import ConfigError, Settings, load_sender


def app_version() -> str:
    return os.environ.get("INVOICES_VERSION", "").strip() or "dev"


def current_config(settings: Settings) -> dict:
    """The configuration that influences records: sender data and retention."""
    try:
        sender = asdict(load_sender(settings.sender_file))
    except ConfigError as e:
        sender = {"error": str(e)}
    return {
        "retention_years": settings.retention_years,
        "backup_keep": settings.backup_keep,
        "sender": sender,
    }


def _changed(conn: sqlite3.Connection, action: str, detail: str) -> bool:
    last = db.last_system_event(conn, action)
    return last is None or last["detail"] != detail


def record_version(conn: sqlite3.Connection) -> None:
    version = app_version()
    with conn:
        if _changed(conn, "version", version):
            db.add_system_event(conn, "version", version)


def record_config(conn: sqlite3.Connection, settings: Settings) -> None:
    detail = json.dumps(current_config(settings), ensure_ascii=False, sort_keys=True)
    with conn:
        if _changed(conn, "config_changed", detail):
            db.add_system_event(conn, "config_changed", detail)


def record_runtime(conn: sqlite3.Connection, settings: Settings) -> None:
    record_version(conn)
    record_config(conn, settings)


def control_run(conn: sqlite3.Connection, kind: str, ok: bool, detail: str = "") -> None:
    db.add_control_run(conn, kind, ok, detail, app_version())
