"""SQLite storage. The schema enforces that issued invoices keep their identity:
number, amounts, dates and the archived PDF hash can never be changed or deleted.
Uploaded expense documents are equally fixed; only their booking data can be corrected."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id              INTEGER PRIMARY KEY,
    number          TEXT NOT NULL UNIQUE,
    issue_date      TEXT NOT NULL,             -- ISO yyyy-mm-dd
    service_date    TEXT NOT NULL,             -- as printed
    due_date        TEXT,                      -- ISO, NULL for imports without one
    customer_name   TEXT NOT NULL,
    customer_street TEXT NOT NULL DEFAULT '',
    customer_city   TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    amount_cents    INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'paid', 'cancelled')),
    paid_date       TEXT,
    notes           TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL CHECK (source IN ('generated', 'imported')),
    pdf_path        TEXT NOT NULL UNIQUE,      -- relative to the archive dir
    pdf_sha256      TEXT NOT NULL,
    pdf_size        INTEGER NOT NULL,
    payload_json    TEXT NOT NULL,             -- full input incl. sender snapshot
    retain_until    TEXT NOT NULL,             -- ISO date
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    invoice_id  INTEGER NOT NULL REFERENCES invoices(id),
    at          TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_invoices_issue_date ON invoices(issue_date);
CREATE INDEX IF NOT EXISTS idx_events_invoice ON events(invoice_id);

CREATE TRIGGER IF NOT EXISTS invoices_no_delete
BEFORE DELETE ON invoices
BEGIN
    SELECT RAISE(ABORT, 'archived invoices cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS invoices_immutable
BEFORE UPDATE OF number, issue_date, service_date, due_date, customer_name,
    customer_street, customer_city, title, description, amount_cents, source,
    pdf_path, pdf_sha256, pdf_size, payload_json, retain_until, created_at
ON invoices
BEGIN
    SELECT RAISE(ABORT, 'archived invoice fields are immutable');
END;

CREATE TRIGGER IF NOT EXISTS events_append_only_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS events_append_only_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TABLE IF NOT EXISTS expenses (
    id                INTEGER PRIMARY KEY,
    vendor            TEXT NOT NULL DEFAULT '',
    invoice_number    TEXT NOT NULL DEFAULT '',
    expense_date      TEXT,                      -- ISO, date on the document
    amount_cents      INTEGER CHECK (amount_cents IS NULL OR amount_cents > 0),
    category          TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'paid'
                      CHECK (status IN ('paid', 'open', 'void')),
    paid_date         TEXT,                      -- ISO, NULL = same as expense_date
    notes             TEXT NOT NULL DEFAULT '',
    reviewed          INTEGER NOT NULL DEFAULT 0 CHECK (reviewed IN (0, 1)),
    doc_path          TEXT NOT NULL UNIQUE,      -- relative to the expenses dir
    doc_sha256        TEXT NOT NULL UNIQUE,
    doc_size          INTEGER NOT NULL,
    doc_type          TEXT NOT NULL CHECK (doc_type IN ('pdf', 'jpg', 'png')),
    original_filename TEXT NOT NULL DEFAULT '',
    doc_text          TEXT NOT NULL DEFAULT '',  -- PDF text layer, for search
    suggestion_json   TEXT NOT NULL,             -- values read from the document at upload
    retain_until      TEXT NOT NULL,             -- ISO date
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expense_events (
    id          INTEGER PRIMARY KEY,
    expense_id  INTEGER NOT NULL REFERENCES expenses(id),
    at          TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_expenses_expense_date ON expenses(expense_date);
CREATE INDEX IF NOT EXISTS idx_expense_events_expense ON expense_events(expense_id);

CREATE TRIGGER IF NOT EXISTS expenses_no_delete
BEFORE DELETE ON expenses
BEGIN
    SELECT RAISE(ABORT, 'archived expenses cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS expenses_immutable
BEFORE UPDATE OF doc_path, doc_sha256, doc_size, doc_type, original_filename, doc_text,
    suggestion_json, retain_until, created_at
ON expenses
BEGIN
    SELECT RAISE(ABORT, 'archived expense documents are immutable');
END;

CREATE TRIGGER IF NOT EXISTS expense_events_append_only_update
BEFORE UPDATE ON expense_events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS expense_events_append_only_delete
BEFORE DELETE ON expense_events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def add_event(conn: sqlite3.Connection, invoice_id: int, action: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO events (invoice_id, at, action, detail) VALUES (?, ?, ?, ?)",
        (invoice_id, now_iso(), action, detail),
    )


def add_expense_event(conn: sqlite3.Connection, expense_id: int, action: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO expense_events (expense_id, at, action, detail) VALUES (?, ?, ?, ?)",
        (expense_id, now_iso(), action, detail),
    )
