# Plan and architecture

## Goals

1. **Create** an invoice from a few fields and get the finished PDF, rendered exactly like the
   first hand-made invoice (`rechnung_branded.py`, pixel-identical output).
2. **Track** every invoice: number, dates, customer, amount, payment status, notes, download.
3. **Archive** invoices for 10 years, tamper-evident, on the home server, with backups that can
   later be copied off-site.

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

## Archive rules

- PDFs are stored under `data/archive/<year>/Rechnung_<nummer>_<kunde>.pdf`, written with exclusive
  create, fsynced, and made read-only (0444). The DB row and file are committed together.
- Nothing is ever regenerated. Changing `sender.toml` only affects future invoices.
- Every detail page and every backup re-verifies the SHA-256 of the stored PDFs.
- `retain_until` = 31.12. of (issue year + `INVOICES_RETENTION_YEARS`, default 10).
  Note: since 2025 the statutory period for Buchungsbelege such as outgoing invoices is 8 years
  (BEG IV); 10 years is the safer default you asked for. Nothing is deleted automatically.
- Cancelled invoices stay in the archive with status "Storniert" and a reason.

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

Later (not built):
- Off-site backup target (decide: see BACKUP.md options).
- Multiple line items, VAT (Regelbesteuerung) once no longer Kleinunternehmer.
- E-invoice formats (ZUGFeRD/XRechnung). B2B e-invoicing obligations apply to
  Kleinunternehmer for receiving only; issuing stays optional for them.
- Sending invoices by email.
