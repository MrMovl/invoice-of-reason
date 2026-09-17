"""Verification of the hash chains and protective triggers (GoBD Rz. 110). See db.py for how
the chains are built."""

from __future__ import annotations

import sqlite3

from . import db

TABLE_NAMES = {
    "events": "Rechnungsverlauf",
    "expense_events": "Belegverlauf",
    "system_events": "Systemprotokoll",
    "control_runs": "Kontrollprotokoll",
    "external_receipts": "Umsätze außerhalb des Programms",
}


def verify_chains(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return (where, problem) for every broken chain, stale record state or missing trigger."""
    problems = [("Datenbank", f"Schutz-Trigger fehlt: {name}") for name in db.missing_triggers(conn)]
    for table in db.CHAINED_TABLES:
        # Databases from before the hash chain (e.g. restored old backups) have nothing to check.
        if "hash" in db._columns(conn, table):
            problems += _verify_table(conn, table)
    for table, (record_table, ref) in db.RECORD_OF.items():
        if "state_hash" in db._columns(conn, table):
            problems += _verify_states(conn, table, record_table, ref)
    return problems


def _verify_table(conn: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    prev = ""
    expected_id = 1
    for row in conn.execute(f"SELECT * FROM {table} ORDER BY id"):
        values = {k: row[k] for k in row.keys()}
        where = f"{TABLE_NAMES[table]} #{row['id']}"
        if row["id"] != expected_id:
            return [(where, f"Eintrag #{expected_id} fehlt")]
        if db.chain_hash(prev, values) != row["hash"]:
            return [(where, "Hash-Kette unterbrochen (Eintrag verändert)")]
        prev = row["hash"]
        expected_id += 1
    return []


def _verify_states(conn: sqlite3.Connection, table: str, record_table: str, ref: str) -> list[tuple[str, str]]:
    label = "Rechnung" if record_table == "invoices" else "Beleg"
    key = "number" if record_table == "invoices" else "id"
    latest = {
        r[ref]: r["state_hash"] for r in conn.execute(
            f"SELECT {ref}, state_hash FROM {table} WHERE id IN (SELECT MAX(id) FROM {table} GROUP BY {ref})")
    }
    problems = []
    for record in conn.execute(f"SELECT id, {key} FROM {record_table} ORDER BY id"):
        where = f"{label} {record[key]}"
        if record["id"] not in latest:
            problems.append((where, "kein Verlaufseintrag"))
        elif latest[record["id"]] != db.record_state_hash(conn, record_table, record["id"]):
            problems.append((where, "Datensatz ohne protokollierte Änderung verändert"))
    return problems


def chain_heads(conn: sqlite3.Connection) -> dict:
    return {table: db.chain_head(conn, table) for table in db.CHAINED_TABLES
            if "hash" in db._columns(conn, table)}


def compare_heads(conn: sqlite3.Connection, recorded: dict) -> list[tuple[str, str]]:
    """Check that the chains still contain the heads recorded earlier, e.g. in a backup
    manifest. Detects removal of the newest entries, which the chain alone cannot."""
    problems = []
    for table, head in recorded.items():
        if not head["id"]:
            continue
        row = conn.execute(f"SELECT hash FROM {table} WHERE id = ?", (head["id"],)).fetchone()
        if row is None or row["hash"] != head["hash"]:
            problems.append((f"{TABLE_NAMES.get(table, table)} #{head['id']}",
                             "weicht vom Stand im Backup ab"))
    return problems
