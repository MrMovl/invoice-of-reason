"""Datenüberlassung (GoBD Rz. 128, 167 and Anlage): a ZIP the tax office can load into its audit
software without extra tools. SQLite is not one of the accepted formats, so every table becomes a
CSV described by an index.xml in the GDPdU "Beschreibungsstandard", next to all documents.

Columns are read from the schema, never listed by hand: a column added by a later migration is
exported automatically, and nothing is filtered out (Rz. 173). See docs/EXPORT.md.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import archive, backup, chain, db, expenses, system
from .config import ConfigError, Settings, load_sender

EXPORT_RE = re.compile(r"^gobd-export-(\d{4}|alle)-\d{8}-\d{6}\.zip$")
YEAR_RE = re.compile(r"^\d{4}$")
# The standard requires the DTD next to index.xml. Version 1.6 is backwards compatible with 1.5.
DTD = "gdpdu-01-03-2019.dtd"
DTD_PATH = Path(__file__).with_name(DTD)

# Known tables in export order; any other table found in the schema follows, unfiltered.
TABLES = {
    "invoices": "Ausgangsrechnungen",
    "events": "Änderungsprotokoll der Ausgangsrechnungen",
    "expenses": "Eingangsbelege (Ausgaben)",
    "expense_events": "Änderungsprotokoll der Eingangsbelege",
    "system_events": "Systemprotokoll: Programmversionen, Schemaänderungen, Konfiguration",
    "control_runs": "Kontrollprotokoll: Integritätsprüfungen, Backups, Wiederherstellungstests, Exporte",
}
FOREIGN_KEYS = {"events": ("invoice_id", "invoices"), "expense_events": ("expense_id", "expenses")}
# Columns holding ISO dates (YYYY-MM-DD). Timestamps (UTC, ISO 8601 with time) stay alphanumeric.
DATE_COLUMNS = {"issue_date", "due_date", "paid_date", "retain_until", "expense_date"}
# Money is stored as integer cents; each such column gets a derived euro column right after it.
MONEY_COLUMNS = {"amount_cents": "amount_eur"}

DESCRIPTIONS = {
    "id": "Eindeutige laufende Nummer des Datensatzes",
    "number": "Rechnungsnummer",
    "issue_date": "Rechnungsdatum",
    "service_date": "Leistungsdatum bzw. -zeitraum wie auf der Rechnung gedruckt",
    "due_date": "Fälligkeitsdatum",
    "customer_name": "Kunde",
    "customer_street": "Straße des Kunden",
    "customer_city": "PLZ und Ort des Kunden",
    "title": "Leistungsbezeichnung",
    "description": "Leistungsbeschreibung",
    "amount_cents": "Betrag in Cent (ganze Zahl)",
    "amount_eur": "Betrag in Euro (aus amount_cents abgeleitet)",
    "status": "Zahlungsstatus",
    "paid_date": "Zahlungsdatum",
    "notes": "Interne Notiz",
    "source": "Herkunft: generated = im Programm erstellt, imported = importiert",
    "pdf_path": "Pfad der archivierten PDF im Ordner archive/",
    "pdf_sha256": "SHA-256-Prüfsumme der archivierten PDF",
    "pdf_size": "Dateigröße der PDF in Bytes",
    "payload_json": "Vollständige Eingabedaten inkl. Absenderdaten zum Erstellungszeitpunkt (JSON)",
    "retain_until": "Frühestes Ende der Aufbewahrung (Mindestfrist, läuft nicht ab, solange die Festsetzungsfrist offen ist, § 147 Abs. 3 AO)",
    "created_at": "Erfasst am (UTC)",
    "updated_at": "Zuletzt geändert am (UTC)",
    "invoice_id": "Verweis auf invoices.id",
    "expense_id": "Verweis auf expenses.id",
    "at": "Zeitpunkt (UTC)",
    "action": "Art des Eintrags",
    "detail": "Details, bei Änderungen alter und neuer Wert",
    "vendor": "Lieferant bzw. Rechnungssteller",
    "invoice_number": "Rechnungsnummer des Lieferanten",
    "expense_date": "Belegdatum",
    "category": "Kategorie",
    "reviewed": "Erkannte Werte manuell geprüft (1 = ja, 0 = nein)",
    "doc_path": "Pfad des Belegs im Ordner expenses/",
    "doc_sha256": "SHA-256-Prüfsumme des Belegs",
    "doc_size": "Dateigröße des Belegs in Bytes",
    "doc_type": "Dateityp des Belegs: pdf, jpg, png oder xml (E-Rechnung XRechnung/UBL/CII)",
    "original_filename": "Ursprünglicher Dateiname beim Hochladen",
    "doc_text": "Textebene der PDF bzw. lesbare Fassung der E-Rechnung (für die Suche)",
    "suggestion_json": "Beim Hochladen aus dem Beleg gelesene Werte (JSON), mit Quelle: xml, zugferd, text oder none",
    "kind": "Art der Kontrolle",
    "ok": "Ergebnis (1 = erfolgreich, 0 = fehlgeschlagen)",
    "app_version": "Programmversion",
    "payment_method": "Zahlungsart: bank = Überweisung/Karte, cash = bar, private = privat bezahlt (Einlage), leer = nicht erfasst",
    "hash": "SHA-256 der Hash-Kette: Hash des vorigen Eintrags + Inhalt dieses Eintrags (siehe README)",
    "state_hash": "SHA-256 des Datensatzes (Rechnung bzw. Beleg) nach dieser Änderung",
}


class ExportError(RuntimeError):
    pass


def create_export(settings: Settings, year: str | None = None) -> Path:
    """Create an export and record the run, successful or not, in control_runs."""
    label = year or "alle"
    try:
        target = _create_export(settings, year)
    except Exception as e:
        backup.record_run(settings, "export", False, f"{label}: {e}")
        raise
    backup.record_run(settings, "export", True, target.name)
    return target


def list_exports(settings: Settings) -> list[Path]:
    folder = settings.backup_dir / "exports"
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if EXPORT_RE.match(p.name)), reverse=True)


def export_years(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT substr(issue_date, 1, 4) FROM invoices UNION "
        f"SELECT substr({expenses.booking_date_sql()}, 1, 4) FROM expenses ORDER BY 1 DESC")
    return [r[0] for r in rows if r[0] and YEAR_RE.match(r[0])]


def _create_export(settings: Settings, year: str | None) -> Path:
    if year is not None and not YEAR_RE.match(year):
        raise ExportError("Jahr muss vierstellig sein, z. B. 2026.")
    folder = settings.backup_dir / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = folder / f"gobd-export-{year or 'alle'}-{stamp}.zip"
    if target.exists():
        raise ExportError(f"{target.name} existiert bereits.")

    conn = db.connect(settings.db_path)
    try:
        db.init_db(conn)
        conn.execute("BEGIN")  # one read snapshot for all tables
        # Hand over nothing whose log history is broken: the export must match the records.
        chain_problems = chain.verify_chains(conn)
        tables = [(name, _columns(conn, name), _rows(conn, name, year)) for name in _table_names(conn)]
        conn.rollback()
    finally:
        conn.close()

    rows = {name: r for name, _cols, r in tables}
    documents = [(f"archive/{r['pdf_path']}", settings.archive_dir / r["pdf_path"], r["pdf_sha256"], r["number"])
                 for r in rows["invoices"]]
    documents += [(f"expenses/{r['doc_path']}", settings.expenses_dir / r["doc_path"], r["doc_sha256"],
                   f"Beleg {r['id']}") for r in rows["expenses"]]
    problems = chain_problems + [(label, p) for _arc, path, sha, label in documents
                                 if (p := archive.verify_file(path, sha, missing="Datei fehlt"))]
    if problems:
        raise ExportError("Archiv inkonsistent, Export abgebrochen: "
                          + "; ".join(f"{n}: {p}" for n, p in problems))

    with tempfile.TemporaryDirectory(dir=folder, prefix=".tmp-") as tmp:
        partial = Path(tmp) / target.name
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("index.xml", index_xml(settings, year, [(n, c) for n, c, _r in tables]))
            zf.write(DTD_PATH, DTD)
            zf.writestr("README.txt", README.replace("\n", "\r\n").format(
                zeitraum=f"Jahr {year}" if year else "alle Jahre",
                erstellt=db.now_iso(), version=system.app_version()))
            for name, cols, table_rows in tables:
                zf.writestr(f"{name}.csv", to_csv(cols, table_rows))
            for arcname, path, _sha, _label in documents:
                zf.write(path, arcname)
        partial.rename(target)
    target.chmod(0o440)
    return target


# ── Tables ────────────────────────────────────────────────────────────────


def _table_names(conn: sqlite3.Connection) -> list[str]:
    present = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return [t for t in TABLES if t in present] + [t for t in present if t not in TABLES]


def _columns(conn: sqlite3.Connection, table: str) -> list[tuple[str, str, bool]]:
    """(name, kind, primary key) per exported column, kind being alnum, int, date or money."""
    cols = []
    info = sorted(conn.execute(f'PRAGMA table_info("{table}")'), key=lambda c: (not c["pk"], c["cid"]))
    for c in info:
        name = c["name"]
        if name in DATE_COLUMNS:
            kind = "date"
        elif "INT" in (c["type"] or "").upper():
            kind = "int"
        else:
            kind = "alnum"
        cols.append((name, kind, bool(c["pk"])))
        if name in MONEY_COLUMNS:
            cols.append((MONEY_COLUMNS[name], "money", False))
    return cols


def _rows(conn: sqlite3.Connection, table: str, year: str | None) -> list[sqlite3.Row]:
    booked = expenses.booking_date_sql()
    where = {
        "invoices": "substr(issue_date, 1, 4) = :year",
        "events": "invoice_id IN (SELECT id FROM invoices WHERE substr(issue_date, 1, 4) = :year)",
        "expenses": f"substr({booked}, 1, 4) = :year",
        "expense_events": f"expense_id IN (SELECT id FROM expenses WHERE substr({booked}, 1, 4) = :year)",
    }.get(table) if year else None
    order = "id" if any(c["name"] == "id" for c in conn.execute(f'PRAGMA table_info("{table}")')) else "rowid"
    sql = f'SELECT * FROM "{table}"' + (f" WHERE {where}" if where else "") + f" ORDER BY {order}"
    return conn.execute(sql, {"year": year}).fetchall()


def _value(row: sqlite3.Row, name: str, kind: str) -> str:
    if kind == "money":
        cents = row[next(k for k, v in MONEY_COLUMNS.items() if v == name)]
        if cents is None:
            return ""
        sign = "-" if cents < 0 else ""
        return f"{sign}{abs(cents) // 100},{abs(cents) % 100:02d}"
    value = row[name]
    if value is None:
        return ""
    if isinstance(value, float):
        return repr(value).replace(".", ",")
    if isinstance(value, bytes):
        return value.hex()
    # CR LF is the record delimiter; line breaks inside a text are written as a bare LF so no
    # reader can mistake them for the end of a record.
    return str(value).replace("\r\n", "\n").replace("\r", "\n")


def to_csv(cols: list[tuple[str, str, bool]], rows: list[sqlite3.Row]) -> bytes:
    """Anlage 1.4 defaults: header row, ';', '"' quoting with doubled quotes, CRLF, UTF-8."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, delimiter=";", quotechar='"', doublequote=True,
                        quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([name for name, _kind, _pk in cols])
    for row in rows:
        writer.writerow([_value(row, name, kind) for name, kind, _pk in cols])
    return buf.getvalue().encode("utf-8")


# ── index.xml ─────────────────────────────────────────────────────────────


CRLF_MARK = "@@CRLF@@"


def index_xml(settings: Settings, year: str | None, tables: list[tuple[str, list]]) -> bytes:
    """Describe the CSVs in the GDPdU Beschreibungsstandard (gdpdu-01-09-2004.dtd)."""
    root = ET.Element("DataSet")
    ET.SubElement(root, "Version").text = "1.0"
    try:
        sender = load_sender(settings.sender_file)
        supplier_name, location = sender.name, sender.city
    except ConfigError:
        supplier_name = location = ""
    supplier = ET.SubElement(root, "DataSupplier")
    ET.SubElement(supplier, "Name").text = supplier_name
    ET.SubElement(supplier, "Location").text = location
    ET.SubElement(supplier, "Comment").text = (
        f"Datenüberlassung nach GoBD, {'Jahr ' + year if year else 'alle Jahre'}, "
        f"erstellt {db.now_iso()}, Programmversion {system.app_version()}")
    media = ET.SubElement(root, "Media")
    ET.SubElement(media, "Name").text = f"Rechnungsarchiv {year or 'alle Jahre'}"
    for name, cols in tables:
        table = ET.SubElement(media, "Table")
        ET.SubElement(table, "URL").text = f"{name}.csv"
        ET.SubElement(table, "Name").text = name
        ET.SubElement(table, "Description").text = TABLES.get(name, "")
        ET.SubElement(table, "UTF8")
        ET.SubElement(table, "DecimalSymbol").text = ","
        ET.SubElement(table, "DigitGroupingSymbol").text = "."
        # Every CSV starts with a header row; without this the audit software imports it as data.
        ET.SubElement(ET.SubElement(table, "Range"), "From").text = "2"
        layout = ET.SubElement(table, "VariableLength")
        ET.SubElement(layout, "ColumnDelimiter").text = ";"
        ET.SubElement(layout, "RecordDelimiter").text = CRLF_MARK
        ET.SubElement(layout, "TextEncapsulator").text = '"'
        for col, kind, pk in cols:
            column = ET.SubElement(layout, "VariablePrimaryKey" if pk else "VariableColumn")
            ET.SubElement(column, "Name").text = col
            ET.SubElement(column, "Description").text = DESCRIPTIONS.get(col, "")
            if kind == "date":
                ET.SubElement(ET.SubElement(column, "Date"), "Format").text = "YYYY-MM-DD"
            elif kind == "money":
                ET.SubElement(ET.SubElement(column, "Numeric"), "Accuracy").text = "2"
            elif kind == "int":
                ET.SubElement(column, "Numeric")
            else:
                ET.SubElement(column, "AlphaNumeric")
        if name in FOREIGN_KEYS:
            col, ref = FOREIGN_KEYS[name]
            fk = ET.SubElement(layout, "ForeignKey")
            ET.SubElement(fk, "Name").text = col
            ET.SubElement(fk, "References").text = ref
    ET.indent(root)
    # A literal CR LF in element text would be normalised to LF by any XML parser.
    body = ET.tostring(root, encoding="unicode").replace(CRLF_MARK, "&#13;&#10;")
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE DataSet SYSTEM "{DTD}">\n{body}\n'
            ).encode("utf-8")


README = """\
Datenüberlassung nach GoBD (Rz. 128, 167 und Anlage)
=====================================================

Zeitraum: {zeitraum}
Erstellt: {erstellt} (UTC)
Programmversion: {version}

Inhalt
------
index.xml             Beschreibung aller Tabellen nach dem GDPdU-Beschreibungsstandard
                      (DTD gdpdu-01-03-2019.dtd, Version 1.6, liegt bei). Datensätze beginnen in
                      Zeile 2 jeder CSV-Datei (Range/From = 2), Zeile 1 enthält die Feldnamen.
gdpdu-01-03-2019.dtd  DTD des Beschreibungsstandards
invoices.csv          Ausgangsrechnungen
events.csv            Änderungsprotokoll der Ausgangsrechnungen
expenses.csv          Eingangsbelege (Ausgaben)
expense_events.csv    Änderungsprotokoll der Eingangsbelege
system_events.csv     Systemprotokoll (Programmversionen, Schemaänderungen, Konfiguration)
control_runs.csv      Kontrollprotokoll (Integritätsprüfungen, Backups, Wiederherstellungstests,
                      Exporte)
archive/              Die archivierten Rechnungs-PDFs, Pfad wie in invoices.pdf_path
expenses/             Die hochgeladenen Belege im Originalformat, Pfad wie in expenses.doc_path

Weitere Tabellen, die eine spätere Programmversion anlegt, liegen ebenfalls als CSV bei und sind in
index.xml beschrieben. Alle Spalten der Datenbank sind enthalten, es wird nichts herausgefiltert.

Format der CSV-Dateien
----------------------
- Zeichensatz UTF-8 (ohne BOM)
- Erste Zeile: Feldnamen
- Feldtrenner Semikolon (;), Datensatztrenner CR LF
- Textbegrenzer " (Anführungszeichen im Text werden verdoppelt); Texte können Zeilenumbrüche
  enthalten, stehen dann in Anführungszeichen und verwenden als Zeilenumbruch nur LF, sodass
  CR LF ausschließlich Datensätze trennt
- Dezimaltrenner Komma, Tausendertrenner Punkt (wird nicht verwendet)
- Datumsfelder im Format JJJJ-MM-TT (ISO 8601), z. B. 2026-09-17
- Zeitstempel (at, created_at, updated_at) in UTC nach ISO 8601, z. B. 2026-09-17T08:30:00+00:00
- Leere Felder bedeuten "kein Wert"

Beträge
-------
amount_cents ist der Betrag in Cent als ganze Zahl (so gespeichert). amount_eur ist daraus
abgeleitet: derselbe Betrag in Euro mit Dezimalkomma, z. B. 700,00. Die Ausgangsrechnungen fallen
unter die Steuerbefreiung für Kleinunternehmer (§ 19 UStG); es wird keine Umsatzsteuer ausgewiesen,
die Beträge sind Endbeträge.

Verknüpfungen
-------------
events.invoice_id          -> invoices.id
expense_events.expense_id  -> expenses.id
invoices.pdf_path          -> Datei archive/<pdf_path>, Prüfsumme invoices.pdf_sha256
expenses.doc_path          -> Datei expenses/<doc_path>, Prüfsumme expenses.doc_sha256
Alle Dateien wurden vor dem Export gegen ihre SHA-256-Prüfsumme geprüft.

Auswahl bei Export eines Jahres
-------------------------------
Rechnungen nach Rechnungsdatum (issue_date); Belege nach Buchungsdatum: Zahlungsdatum
(paid_date), sonst Belegdatum (expense_date), sonst Tag des Hochladens. Die Änderungsprotokolle
enthalten alle Einträge zu den exportierten Rechnungen und Belegen. System- und Kontrollprotokoll
sind immer vollständig.

Statuswerte
-----------
invoices.status:  open = offen, paid = bezahlt, cancelled = storniert
expenses.status:  paid = bezahlt, open = offen, void = verworfen (z. B. Fehl-Upload; Grund in notes
                  bzw. im Änderungsprotokoll)
invoices.source:  generated = im Programm erstellt, imported = vor Einführung des Programms
                  erstellte Rechnung, Original-PDF unverändert übernommen (Grund im Verlauf)

Einträge in den Protokollen (action bzw. kind)
----------------------------------------------
events:          created = erstellt und archiviert, imported = importiert,
                 status:open / status:paid / status:cancelled = Statusänderung (alt -> neu),
                 notes = Notiz geändert (alter und neuer Text),
                 sealed = Zustand beim Einführen der Hash-Kette festgehalten
expense_events:  uploaded = Beleg hochgeladen, reviewed = erkannte Werte erstmals geprüft,
                 updated = Buchungsdaten geändert (alter und neuer Wert),
                 sealed = Zustand beim Einführen der Hash-Kette festgehalten
system_events:   version = Programmversion in Betrieb genommen,
                 schema_migration = Datenbankschema geändert,
                 config_changed = Konfiguration (Absenderdaten, Aufbewahrung) geändert,
                 trigger_missing = beim Start fehlende Schutz-Trigger festgestellt und
                 wiederhergestellt
control_runs:    verify = Integritätsprüfung, backup = Backup,
                 restore_test = Wiederherstellungstest, export = Datenexport
                 (dieser Export selbst wird erst nach seiner Erstellung protokolliert)

Rechnungen, Belege und Protokolleinträge können im Programm nicht gelöscht werden; die
identitätsbildenden Felder sind unveränderlich.

Hash-Kette
----------
Die vier Protokolltabellen sind jeweils eine Hash-Kette. hash = SHA-256 über (hash des Eintrags
mit der nächstkleineren id, als Hex-Text; beim ersten Eintrag leer) gefolgt von der kanonischen
Darstellung des Eintrags: JSON-Objekt aller Spalten außer hash, Schlüssel sortiert, ohne leere
Werte (NULL und leerer Text), ohne Leerzeichen, UTF-8. state_hash in events und expense_events ist
SHA-256 über die kanonische Darstellung des Datensatzes aus invoices bzw. expenses nach der
Änderung, ohne die Spalte updated_at. Der jeweils letzte Eintrag je Rechnung bzw. Beleg muss zum
exportierten Datensatz passen. Der Export wurde nur erstellt, nachdem alle Ketten geprüft waren.
"""
