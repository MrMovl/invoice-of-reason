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
  Only `status`, `paid_date`, `payment_method`, `notes` can change.
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

## Receipts outside the tool

`external_receipts` is an append-only table (triggers, hash chain, export) of receipts belonging to
the same Unternehmer but not invoiced here (§ 2 Abs. 1 Satz 2 UStG). Entered by date, amount,
source and note on the "Extern" page; corrections are counter-entries with a mandatory note. They
count in `turnover.status` for the current and previous year and can be the crossing receipt. They
are deliberately **not** part of `cash_summary`: the records of those activities live elsewhere.

## Cancellation documents

- Cancelling an invoice that was never sent (only while unpaid) stays a status change with a reason.
- An invoice the customer received is cancelled with a Stornorechnung: `archive.cancel_invoice`
  writes a row in `invoices` with `kind = 'cancellation'`, `cancels_invoice_id`, the next number of
  the same sequence and a negative amount, renders the PDF through the invoice layout
  (`pdf.Cancellation`) and archives it write-once. Original and document are committed together.
- The original keeps `paid_date` and `payment_method`; its status becomes `cancelled` and can no
  longer change. On the cancellation row the status is the refund: `cancelled` nothing to refund,
  `open` refund due, `paid` refunded (`paid_date` = payout).
- Every sum over `invoices` filters by `kind`: income counts receipts of invoices including later
  cancelled ones, refunds are a separate line in the year of payout, the turnover monitor counts
  receipts only and subtracts no refunds. See docs/CANCELLATION.md.

## Turnover limit monitor (§ 19 UStG)

`turnover.py` counts paid invoices by `paid_date` year (receipts), projects open invoices, and
applies the limits since 2025: previous year above 25,000 € ends the status for the whole year;
the current year limit is 100,000 €, or 25,000 € in the founding year (`INVOICES_FOUNDING_YEAR`),
with immediate effect from the crossing receipt. The invoice list and the new-invoice form show
received, open, limit and headroom, warn at 80 % and when the current year passes 25,000 €.
Issuing is refused when received + open + the new invoice exceed the limit, or the previous year
exceeded 25,000 €, unless overridden with a reason that is logged in the `created` event. Imports
are not checked. Only receipts recorded in the tool count.

## Expenses

- Upload one or many PDF/JPEG/PNG files or XML e-invoices (max. 20 MB each). Identical files are
  rejected by hash. The type is detected from the content, not the file name.
- Documents go to `data/expenses/<upload year>/<YYYYMMDD>_<original name>_<sha8>.<ext>`, same
  write-once rules as invoice PDFs.
- PDFs with a text layer are read with `pdftotext -layout` (first 5 pages). `extract.py` suggests
  vendor (legal form like GmbH, else first line), invoice number, invoice date and the gross total
  (labelled totals like "Gesamtbetrag"/"Zahlbetrag" win, net/VAT lines are skipped, else the largest
  amount with a currency). Scans and photos get no suggestion. No OCR, nothing leaves the server.
- E-invoices (`einvoice.py`): XRechnung/EN 16931 XML in UBL 2.1 (Invoice, CreditNote) or CII is
  archived byte for byte (`doc_type = 'xml'`); anything else that looks like XML is rejected, as is
  XML with DOCTYPE/ENTITY declarations. Seller, number, date and payable amount become the
  suggestion (other currencies are not prefilled; credit notes are flagged, amount stays positive).
  The detail page renders the invoice (parties, dates, lines, totals) from the archived XML; "XML
  ansehen" serves the raw file as `text/plain`, never as renderable XML. `doc_text` holds a plain
  text rendering for search.
- ZUGFeRD/Factur-X PDFs: `pdfdetach` looks for an attached `factur-x.xml`, `zugferd-invoice.xml` or
  `xrechnung.xml`; its values win over the text-layer heuristics. The PDF stays the archived
  document. `suggestion_json.source` records `xml`, `zugferd`, `text` or `none`.
- Every upload stays "zu prüfen" until saved once; "Speichern und nächster" walks the review queue.
  Uploads unreviewed for more than 10 days are marked "über 10 Tage ungeprüft" (GoBD Rz. 47).
- Saving requires a category, and for paid expenses a payment method (bank/card, cash, paid
  privately), unless the expense is voided (GoBD Rz. 50, 79).
- Capital assets: `treatment` ('' | 'asset') is set by hand when reviewing. Assets are excluded from
  the expense total of the overview and shown separately; the surplus is labelled "vor AfA". The
  review form shows a non-blocking hint when the (gross) amount exceeds `GWG_LIMIT_NET_CENTS`
  (800 € net, § 6 Abs. 2 EStG) and the field is empty. Asset register and AfA stay outside the tool.
- Reverse charge (§ 13b UStG): `reverse_charge` ('' | '13b') is set by hand when reviewing. At upload
  `expenses.charged_vat` first checks whether the document charges VAT (e-invoice tax total > 0 or
  category S with a rate > 0; a VAT line with rate and amount > 0 in the PDF text). If it does, the
  amount is recorded in `suggestion_json.vat_charged` and there is no § 13b hint, because a supplier
  abroad may charge German VAT (OSS) and still print conditional reverse charge boilerplate.
  Otherwise `suggestion_json.reverse_charge_hint` records why it may apply (e-invoice VAT category
  AE, seller country ≠ DE, wording such as "reverse charge"); it is shown, never applied.
  The expenses page sums marked, non-void expenses per quarter of the selected year by invoice
  date as the tax base, with 19 % as a labelled orientation value. Filter "Nur § 13b UStG".
  For an invoice in a foreign currency the review form points to the monthly average rate published
  by the BMF (§ 16 Abs. 6 UStG); the tool stores one EUR amount.
- Overview on the archive page: income (paid invoices) vs. expenses (paid expenses except assets) by
  payment date, per selected year, which matches the cash basis of an EÜR before AfA. An expense without a paid date counts
  on its invoice date.

## Archive rules

- PDFs are stored under `data/archive/<year>/Rechnung_<nummer>_<kunde>.pdf`, written with exclusive
  create, fsynced, and made read-only (0444). The DB row and file are committed together.
- Nothing is ever regenerated. Changing `sender.toml` only affects future invoices.
- Every detail page and every backup re-verifies the SHA-256 of the stored PDFs.
- `retain_until` = 31.12. of (issue year + `INVOICES_RETENTION_YEARS`, default 10). It is a
  minimum, not an end date, and nothing is ever deleted. Since 2025 Buchungsbelege such as invoices
  need 8 years (BEG IV); 10 is a deliberate conservative default, see [GOBD.md](GOBD.md#retention).
- Cancelled invoices stay in the archive with status "Storniert" and a reason.
- Invoice numbers follow `YYYY-NNN` without gaps. The archive page lists gaps, numbers outside the
  scheme and numbers whose year differs from the issue date (`archive.number_gaps`). Creating an
  invoice that would add such a finding needs "Abweichende Nummer bewusst verwenden" plus a reason,
  which is logged in the `created` event.
- Marking an invoice paid requires the payment method; setting it back clears it (logged).

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

Done:
- New invoice form with PDF preview, auto numbering (`YYYY-NNN`), customer autofill,
  "use as template", Leistungszeitraum, configurable Zahlungsziel.
- Archive list with year/status/search filters, totals, overdue marker.
- Detail page: download/view, mark paid/open, cancel (with or without a Stornorechnung), record
  refunds, notes, integrity status, history.
- Invoices enter the archive through the create form. Invoices issued before the tool existed are
  archived from their original PDF with the `invoices import-invoice` CLI (source "imported"): no
  web route, reason required, number and amount cross-checked with the PDF text, typed
  confirmation, same write-once rules and hash chain as created invoices.
- Backups: automatic daily, manual button, download, rotation, verify, restore CLI. They include
  `data/dokumentation/`, where part 6 of the Verfahrensdokumentation and similar documents live.
- Expenses: upload, text-layer and e-invoice (XRechnung, ZUGFeRD) suggestions, review queue,
  categories, payment method, § 13b and asset marking, search in document text, income/expense
  summary. Included in verify and backups.
- GoBD: hash chain over all logs, control log, Z3 export, Programmidentität, retention per record.
- § 19 UStG: turnover limit monitor with receipts recorded outside the tool.

Later (not built), see docs/GOBD.md for the full list:
- Off-site backup target (decide: see BACKUP.md options).
- Multiple line items, VAT (Regelbesteuerung) once no longer Kleinunternehmer.
- Issuing e-invoices (ZUGFeRD/XRechnung). B2B e-invoicing obligations apply to
  Kleinunternehmer for receiving only (done); issuing stays optional for them.
- Sending invoices by email.
- OCR for scanned expense receipts (tesseract), expense export for the EÜR (Anlage EÜR lines).
