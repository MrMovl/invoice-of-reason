"""SQLite storage. The schema enforces that issued invoices keep their identity:
number, amounts, dates and the archived PDF hash can never be changed or deleted.
Uploaded expense documents are equally fixed; only their booking data can be corrected."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
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


# Schema changes after the initial schema. Each entry runs once, in order, inside one
# transaction, and is recorded in system_events (GoBD Rz. 142: migrations are documented).
# PRAGMA user_version holds the number of applied migrations. Never edit an applied entry.
MIGRATIONS: list[tuple[str, str | Callable[[sqlite3.Connection], None]]] = [
    (
        "system_events and control_runs",
        """
        CREATE TABLE system_events (
            id      INTEGER PRIMARY KEY,
            at      TEXT NOT NULL,
            action  TEXT NOT NULL,             -- version, schema_migration, config_changed
            detail  TEXT NOT NULL DEFAULT ''
        );
        CREATE TRIGGER system_events_append_only_update BEFORE UPDATE ON system_events
        BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
        CREATE TRIGGER system_events_append_only_delete BEFORE DELETE ON system_events
        BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;

        CREATE TABLE control_runs (
            id          INTEGER PRIMARY KEY,
            at          TEXT NOT NULL,
            kind        TEXT NOT NULL,         -- verify, backup, restore_test, export
            ok          INTEGER NOT NULL CHECK (ok IN (0, 1)),
            detail      TEXT NOT NULL DEFAULT '',
            app_version TEXT NOT NULL DEFAULT ''
        );
        CREATE TRIGGER control_runs_append_only_update BEFORE UPDATE ON control_runs
        BEGIN SELECT RAISE(ABORT, 'control runs are append-only'); END;
        CREATE TRIGGER control_runs_append_only_delete BEFORE DELETE ON control_runs
        BEGIN SELECT RAISE(ABORT, 'control runs are append-only'); END;
        """,
    ),
    ("hash chain over all logs", lambda conn: _introduce_hash_chain(conn)),
]


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def init_db(conn: sqlite3.Connection) -> None:
    existing = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'invoices'").fetchone()
    missing = missing_triggers(conn) if existing else []
    conn.executescript(SCHEMA)
    conn.commit()
    migrate(conn)
    if missing:
        # The schema script has just restored them. Keep a permanent trace (GoBD Rz. 108).
        with conn:
            add_system_event(conn, "trigger_missing", ", ".join(missing))


def migrate(conn: sqlite3.Connection) -> None:
    for number, (description, sql) in enumerate(MIGRATIONS, start=1):
        # Re-read inside the loop: another process may have migrated concurrently.
        if schema_version(conn) >= number:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            if schema_version(conn) >= number:
                conn.rollback()
                continue
            if callable(sql):
                sql(conn)
            else:
                for statement in _statements(sql):
                    conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {number}")
            add_system_event(conn, "schema_migration", f"{number}: {description}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def _statements(sql: str) -> list[str]:
    """Split a migration script into statements; trigger bodies contain ';' too."""
    statements, current = [], ""
    for line in sql.splitlines(keepends=True):
        current += line
        if sqlite3.complete_statement(current):
            if current.strip():
                statements.append(current.strip())
            current = ""
    if current.strip():
        raise ValueError(f"incomplete migration statement: {current.strip()[:80]}")
    return statements


# ── Hash chain (GoBD Rz. 110) ──────────────────────────────────────────────
#
# Triggers stop accidental changes, but anyone with the database file can drop them. Every log
# table is therefore a hash chain: each row stores sha256(previous hash + row content). Events on
# invoices and expenses also store the hash of the record's full state after the change, so a
# silent UPDATE of a record or an edited, inserted or removed log entry breaks verification.
# Removing the newest entries is only detectable against a copy of the chain head, which every
# backup manifest records.
#
# Canonical form: JSON of all columns, sorted, with NULL and '' omitted. New columns that default
# to NULL or '' therefore leave existing hashes valid; other defaults need a re-seal migration.

CHAINED_TABLES = ("events", "expense_events", "system_events", "control_runs")
RECORD_OF = {"events": ("invoices", "invoice_id"), "expense_events": ("expenses", "expense_id")}
UNHASHED_RECORD_COLUMNS = ("updated_at",)


def canonical(values: dict) -> bytes:
    kept = {k: v for k, v in values.items() if v is not None and v != ""}
    return json.dumps(kept, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def chain_hash(prev_hash: str, row: dict) -> str:
    content = {k: v for k, v in row.items() if k != "hash"}
    return hashlib.sha256(prev_hash.encode() + canonical(content)).hexdigest()


def record_state_hash(conn: sqlite3.Connection, table: str, record_id: int) -> str:
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    if row is None:
        return ""
    state = {k: row[k] for k in row.keys() if k not in UNHASHED_RECORD_COLUMNS}
    return hashlib.sha256(canonical(state)).hexdigest()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _append(conn: sqlite3.Connection, table: str, values: dict) -> None:
    """Insert a log row, chained to the previous one when the table has a hash column."""
    if not conn.in_transaction:
        # Take the write lock before reading the chain head, so two processes cannot both
        # append to the same head.
        conn.execute("BEGIN IMMEDIATE")
    columns = _columns(conn, table)
    if "hash" in columns:
        if table in RECORD_OF:
            record_table, ref = RECORD_OF[table]
            values["state_hash"] = record_state_hash(conn, record_table, values[ref])
        head = conn.execute(f"SELECT id, hash FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
        values = {"id": head["id"] + 1 if head else 1, **values}
        values["hash"] = chain_hash(head["hash"] if head else "", values)
    names = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", tuple(values.values()))


def chain_head(conn: sqlite3.Connection, table: str) -> dict:
    head = conn.execute(f"SELECT id, hash FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
    return {"id": head["id"], "hash": head["hash"]} if head else {"id": 0, "hash": ""}


def _introduce_hash_chain(conn: sqlite3.Connection) -> None:
    for table in CHAINED_TABLES:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN hash TEXT NOT NULL DEFAULT ''")
        if table in RECORD_OF:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN state_hash TEXT NOT NULL DEFAULT ''")
    # Existing entries get their hashes once. Their content is not changed; the append-only
    # triggers are lifted for this and restored within the same transaction.
    triggers = {
        name: sql for name, sql in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name IN (?, ?, ?, ?)",
            ("events_append_only_update", "expense_events_append_only_update",
             "system_events_append_only_update", "control_runs_append_only_update"))
    }
    for name in triggers:
        conn.execute(f"DROP TRIGGER {name}")
    for table in CHAINED_TABLES:
        prev = ""
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall():
            values = dict(zip(row.keys(), row)) if isinstance(row, sqlite3.Row) else dict(row)
            prev = chain_hash(prev, values)
            conn.execute(f"UPDATE {table} SET hash = ? WHERE id = ?", (prev, values["id"]))
    for sql in triggers.values():
        conn.execute(sql)
    # Seal the current state of every record, so later silent changes are detectable.
    for (invoice_id,) in conn.execute("SELECT id FROM invoices ORDER BY id").fetchall():
        add_event(conn, invoice_id, "sealed", "Hash-Kette eingeführt")
    for (expense_id,) in conn.execute("SELECT id FROM expenses ORDER BY id").fetchall():
        add_expense_event(conn, expense_id, "sealed", "Hash-Kette eingeführt")


def expected_triggers(version: int) -> set[str]:
    """Names of all protective triggers the schema defines up to a migration version."""
    scripts = [SCHEMA] + [sql for _desc, sql in MIGRATIONS[:version] if isinstance(sql, str)]
    pattern = re.compile(r"CREATE TRIGGER (?:IF NOT EXISTS )?(\w+)", re.IGNORECASE)
    return {name for script in scripts for name in pattern.findall(script)}


def missing_triggers(conn: sqlite3.Connection) -> list[str]:
    present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    return sorted(expected_triggers(schema_version(conn)) - present)


def add_event(conn: sqlite3.Connection, invoice_id: int, action: str, detail: str = "") -> None:
    _append(conn, "events", {"invoice_id": invoice_id, "at": now_iso(), "action": action, "detail": detail})


def add_expense_event(conn: sqlite3.Connection, expense_id: int, action: str, detail: str = "") -> None:
    _append(conn, "expense_events",
            {"expense_id": expense_id, "at": now_iso(), "action": action, "detail": detail})


def add_system_event(conn: sqlite3.Connection, action: str, detail: str = "") -> None:
    _append(conn, "system_events", {"at": now_iso(), "action": action, "detail": detail})


def last_system_event(conn: sqlite3.Connection, action: str):
    return conn.execute(
        "SELECT * FROM system_events WHERE action = ? ORDER BY id DESC LIMIT 1", (action,)
    ).fetchone()


def add_control_run(conn: sqlite3.Connection, kind: str, ok: bool, detail: str = "",
                    app_version: str = "") -> None:
    with conn:
        _append(conn, "control_runs", {"at": now_iso(), "kind": kind, "ok": int(ok), "detail": detail,
                                       "app_version": app_version})
