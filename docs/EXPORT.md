# Datenexport for a tax audit (Z3)

GoBD Rz. 128, 167 and the Anlage (2024 version): in an audit the tax office may demand all
records on a data carrier in a machine-readable format its audit software (IDEA) reads without
extra tools. SQLite is not one of those formats, so the app writes a ZIP of CSV files described
by an `index.xml`.

```sh
docker compose run --rm app invoices export --year 2025   # one year
docker compose run --rm app invoices export               # everything
```

Or on the "Backups" page, section "Datenexport für die Betriebsprüfung". Files land in
`backups/exports/gobd-export-<year|alle>-YYYYMMDD-HHMMSS.zip` (read-only, not rotated). Every run,
successful or not, is recorded in the control log (`control_runs.kind = export`).

## Contents

| File | |
|------|---|
| `invoices.csv`, `events.csv`, `expenses.csv`, `expense_events.csv`, `system_events.csv`, `control_runs.csv` | One CSV per table. Any table a later migration adds is exported too. |
| `index.xml` | GDPdU "Beschreibungsstandard", `<!DOCTYPE DataSet SYSTEM "gdpdu-01-09-2004.dtd">` |
| `archive/<pdf_path>` | Archived invoice PDFs |
| `expenses/<doc_path>` | Uploaded expense documents, original format |
| `README.txt` | German explanation for the auditor: format, links between tables, status values, event actions |

- **Unfiltered, schema-driven** (Rz. 173): columns come from `PRAGMA table_info`, so a column
  added by a migration appears without code changes. Primary key first, then schema order; the
  CSV header and `index.xml` list the same columns in the same order.
- **CSV format** (Anlage defaults): header row, `;` separator, CRLF record delimiter, `"` text
  qualifier with doubled quotes, UTF-8 without BOM. Line breaks inside a text are written as LF
  so CRLF only ever ends a record.
- **Types**: INTEGER columns are `<Numeric/>`; `issue_date`, `due_date`, `paid_date`,
  `expense_date`, `retain_until` are `<Date>` with format `YYYY-MM-DD`; everything else, including
  UTC timestamps (`at`, `created_at`, `updated_at`, ISO 8601), is `<AlphaNumeric/>`.
- **Money**: `amount_cents` stays the stored integer; a derived `amount_eur` column follows it with
  decimal comma (`<Numeric><Accuracy>2</Accuracy></Numeric>`).
- **Links**: `<ForeignKey>` for `events.invoice_id -> invoices` and
  `expense_events.expense_id -> expenses`.
- **Year filter**: invoices by `issue_date`, expenses by booking date (payment date, else document
  date, else upload day), change logs only for the exported rows. System and control logs are
  always complete.
- **Integrity**: the hash chains, record states and triggers are verified and every document is
  checked against its SHA-256 first; any problem aborts the export. The table rows are read in one SQLite transaction.

The DTD file itself is not shipped: it is published by the BMF / the IDEA vendor (Audicon), and
the auditor's software brings it. `DataSupplier` name and location come from `config/sender.toml`
(empty if it cannot be loaded).
