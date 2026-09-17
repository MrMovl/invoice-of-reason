# Plan and architecture

## Goals

1. **Create** an invoice from a few fields and get the finished PDF, rendered exactly like the
   first hand-made invoice (`rechnung_branded.py`, pixel-identical output).
2. **Track** every invoice: number, dates, customer, amount, payment status, notes, download.
3. **Archive** invoices for 10 years, tamper-evident, on the home server, with backups that can
   later be copied off-site.
4. **Expenses**: upload received invoices and receipts, get amount/date/vendor suggested from the
   PDF, see income vs. expenses per year.

Reachable at `invoices.example.com` behind a login.

## Stack

| Concern      | Choice                                   | Why |
|--------------|------------------------------------------|-----|
| Language     | Python 3.13                              | The existing renderer is reportlab. |
| PDF          | reportlab canvas, ported 1:1             | Keeps the approved design untouched. |
| Web          | Flask + Jinja templates, a few lines of JS | Tiny, server-rendered, no build step. |
| App server   | gunicorn, 1 worker, 4 threads            | Fits the Pi's 1 GB RAM. |
| Storage      | SQLite (WAL) + PDF files on disk         | One user, zero ops, trivially backed up. |
| Runtime      | Docker on `debian:trixie-slim` with Debian's python3-reportlab/flask/gunicorn | PyPI has no armv7 wheels for Pillow; Debian packages are prebuilt for armhf. |
| Deploy       | Cross-build locally, `docker save` over SSH | Same pattern as the other Pi apps; the Pi never builds. |
| Ingress      | Existing Cloudflare Tunnel -> `127.0.0.1:8082` | No open ports, TLS handled by Cloudflare. |

## Data model

- `invoices`: one row per invoice. Identity fields (number, dates, customer, amount, PDF path and
  SHA-256, input snapshot incl. sender data) are protected by SQLite triggers: no UPDATE, no DELETE.
  Only `status`, `paid_date`, `notes` can change.
- `events`: append-only audit log (created, imported, status changes, notes).
- `expenses`: one row per uploaded document (received invoice, receipt). The document itself
  (path, SHA-256, size, type, original filename, extracted text, original suggestion) is immutable
  and the row cannot be deleted. Booking data (vendor, number, date, amount, category, status
  paid/open/void, paid date, notes) stays correctable; `reviewed` flips to 1 on the first save.
  Wrong uploads are set to "Verworfen" (void) instead of deleted.
- `expense_events`: append-only log of uploads and every change with old -> new values.
- `system_events`: append-only log of schema migrations, deployed versions and configuration changes
  (sender data, retention). `PRAGMA user_version` counts applied migrations (`db.MIGRATIONS`).
- `control_runs`: append-only log of verify, backup and restore-test runs with result and version.
- Hash chain: every row of the four log tables stores `hash` = sha256(previous hash + row content).
  Invoice and expense events also store `state_hash`, the hash of the record after the change.
  `verify` recomputes the chains, compares every record with its latest `state_hash` and checks
  that all protective triggers exist, so dropping a trigger and editing the file is detected.
  New columns must default to NULL or '' (omitted from hashes) or come with a re-seal migration.

## Expenses

- Upload one or many PDF/JPEG/PNG files (max. 20 MB each). Identical files are rejected by hash.
- Documents go to `data/expenses/<upload year>/<YYYYMMDD>_<original name>_<sha8>.<ext>`, same
  write-once rules as invoice PDFs.
- PDFs with a text layer are read with `pdftotext -layout` (first 5 pages). `extract.py` suggests
  vendor (legal form like GmbH, else first line), invoice number, invoice date and the gross total
  (labelled totals like "Gesamtbetrag"/"Zahlbetrag" win, net/VAT lines are skipped, else the largest
  amount with a currency). Scans and photos get no suggestion. No OCR, nothing leaves the server.
- Every upload stays "zu prüfen" until saved once; "Speichern und nächster" walks the review queue.
- Overview on the archive page: income (paid invoices) vs. expenses (paid expenses) by payment date,
  per selected year, which matches the cash basis of an EÜR. An expense without a paid date counts
  on its invoice date.

## Archive rules

- PDFs are stored under `data/archive/<year>/Rechnung_<nummer>_<kunde>.pdf`, written with exclusive
  create, fsynced, and made read-only (0444). The DB row and file are committed together.
- Nothing is ever regenerated. Changing `sender.toml` only affects future invoices.
- Every detail page and every backup re-verifies the SHA-256 of the stored PDFs.
- `retain_until` = 31.12. of (issue year + `INVOICES_RETENTION_YEARS`, default 10).
  Note: since 2025 the statutory period for Buchungsbelege such as outgoing invoices is 8 years
  (BEG IV); 10 years is the safer default you asked for. Nothing is deleted automatically.
- Cancelled invoices stay in the archive with status "Storniert" and a reason.

## GoBD

See [GOBD.md](GOBD.md) for the gap analysis against the GoBD and the resulting work plan.

## Backups

See [BACKUP.md](BACKUP.md). Daily tar.gz with SQLite snapshot, all PDFs, and a checksum manifest.
Off-site target is intentionally left open; the backup directory is the pickup point.

## Security

- Single user, scrypt password hash from env, 12h session, Secure/HttpOnly/SameSite cookies.
- CSRF token on every POST, login throttle (5 failures -> 15 min lock per client IP).
- Strict CSP, no inline scripts, `X-Frame-Options: DENY`, `no-store` on authenticated pages.
- Port bound to loopback only; Cloudflare Tunnel is the only ingress.
- Recommended: add a Cloudflare Access policy for `invoices.example.com` as a second factor.
- Personal data (IBAN, tax number, address) lives only in `config/sender.toml` on the server,
  never in git.

## Scope

Done in v0.1:
- New invoice form with PDF preview, auto numbering (`YYYY-NNN`), customer autofill,
  "use as template", Leistungszeitraum, configurable Zahlungsziel.
- Archive list with year/status/search filters, totals, overdue marker.
- Detail page: download/view, mark paid/open/cancelled, notes, integrity status, history.
- Invoices only enter the archive through the create form. The first invoice 2026-001, made
  before the tool existed, was archived once from its original PDF (source "imported").
- Backups: automatic daily, manual button, download, rotation, verify, restore CLI.
- Expenses: upload, text-layer suggestions, review queue, categories, search in document text,
  income/expense summary. Included in verify and backups.

Later (not built):
- Off-site backup target (decide: see BACKUP.md options).
- Multiple line items, VAT (Regelbesteuerung) once no longer Kleinunternehmer.
- E-invoice formats (ZUGFeRD/XRechnung). B2B e-invoicing obligations apply to
  Kleinunternehmer for receiving only; issuing stays optional for them.
- Sending invoices by email.
- OCR for scanned expense receipts (tesseract), expense export for the EÜR (Anlage EÜR lines).
