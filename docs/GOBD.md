# GoBD compliance plan

Gap analysis of this tool against the GoBD (BMF-Schreiben vom 28.11.2019, BStBl I S. 1269, geändert
durch BMF-Schreiben vom 11.03.2024, BStBl I S. 374, und vom 14.07.2025, BStBl 2025 I S. 1502,
anzuwenden ab 14.07.2025, Rz. 185). "Rz." refers to the Randziffern of the GoBD in that current
version. This is an
engineering reading of the text, not tax advice: have a Steuerberater look at the finished
Verfahrensdokumentation once.

Context: Kleinunternehmer (§ 19 UStG), Einnahmen-Überschuss-Rechnung. Rz. 15 (2024 version): the
requirements are judged "auch mit Blick auf die Unternehmensgröße". Proportionality applies, the
principles do not go away.

## Already covered

| Requirement | Rz. | Where |
|---|---|---|
| Unveränderbarkeit, no deletion | 58, 107–111 | SQLite triggers, append-only event tables |
| Keep documents in the received format, no conversion | 119, 131 | write-once files, 0444, SHA-256 |
| Stammdaten history (Beispiel 4) | 59, 111 | `payload_json` holds the sender snapshot; PDFs are never regenerated |
| Unique index per document | 69, 122 | invoice number, expense id, hash |
| Log receipt and processing | 117 | `uploaded` / `reviewed` events |
| Drafts are not retention-relevant | 5 | the preview is never stored |
| Access control | 103 | login, CSRF, Cloudflare |
| Integrity check | 100 | `verify`, before every backup and on detail pages |

## Gaps

### P1: required

1. **Verfahrensdokumentation** (Rz. 34, 102, 106, 151–155). Split in two:
   - *Public, in this repo* (`docs/verfahrensdokumentation/`): technical system documentation
     (data model, meaning of every field, status and event action, Rz. 149), user documentation,
     operations documentation (deploy, backup, restore), IKS description, export format.
   - *Private, not in this repo* (a private repo or `~/invoices/config/` on the server):
     business-specific part: general description of the business, other systems (bank, email,
     paper), Organisationsanweisung for scanning paper receipts (Rz. 136), where bank statements
     and business emails are kept, off-site backup target and key storage, the IKS actually
     performed. The public part links to it.
   - Both versioned (git), both included in backups, kept as long as the data they explain.
2. **Programmidentität** (Rz. 80, 153–154). The deployed git commit is baked into the image,
   every version change is recorded in an append-only `system_events` table and shown in the UI.
   Schema migrations are versioned and logged. Deploys from a dirty working tree are refused.
3. **Complete change log** (Rz. 58, 108, 111).
   - Invoice notes: log old and new text (today only "notes" is logged).
   - Expense notes: log the text instead of "…".
   - No truncation of change details.
   - Status changes log `alt → neu` including the payment date.
   - A reason is required (server-side) for cancelling an invoice and voiding an expense.
   - Configuration changes (sender data, retention years) are logged as `config_changed`.
4. **Datenüberlassung / Z3 export** (Rz. 128, 167, Anlage). SQLite is not one of the formats
   IDEA reads. `invoices export --year` (and a UI button) writes a ZIP: one CSV per table
   (header row, `;`, decimal comma, CRLF, `"` quoting, unfiltered), `index.xml` following the
   Beschreibungsstandard, all documents, a README.
5. **Persistent control log** (Rz. 88, 100). Append-only `control_runs` table for verify, backup,
   restore test and export runs, shown on the backups page. `invoices restore-test` restores a
   backup into a temporary directory, verifies it and records the result.
6. **Off-site backup** (Rz. 103–104). Operations, see BACKUP.md. Decryption key must stay
   available for the whole retention period (Rz. 134). Yearly restore test, recorded as a control
   run.

### P2: strongly recommended

7. **E-invoices: receive** (Rz. 118, 119, 125, 127, 131 as amended 14.07.2025). Receiving B2B
   e-invoices is mandatory since 1.1.2025. Accept XRechnung/UBL/CII XML uploads, store them
   unchanged, read booking suggestions from the XML, show a readable view. ZUGFeRD/Factur-X PDFs
   keep their embedded XML already (no conversion); read suggestions from it too. Reading ZUGFeRD
   is groundwork for issuing it (#15).
   Since 14.07.2025 keeping the structured part of an e-invoice is sufficient; the human-readable
   part of a hybrid invoice (the PDF of a ZUGFeRD invoice) must only be kept if it holds additional
   or different tax-relevant information (Rz. 119, 131). For structured data only a match in
   content, not an image match, is required (Rz. 118). The tool keeps the whole received file,
   which is more than required and covers the case where the PDF carries extra information.
8. **Completeness and timeliness checks** (Rz. 40, 46–50, 79).
   - Gap analysis of invoice numbers per year, warning for numbers outside `YYYY-NNN`.
   - Warning for unreviewed expenses older than 10 days.
   - Category required when reviewing (at least the business assignment, Rz. 50).
   - Payment method (bank / cash / paid privately).
9. **Storno reference** (Rz. 64). Optional Stornorechnung document referencing the original,
   linked both ways. Check with a Steuerberater whether status plus mandatory reason suffices
   for a Kleinunternehmer.
10. **Tamper evidence beyond the file system** (Rz. 110). Hash chain over all event tables,
    checked by `verify`; chain head recorded with every backup (anchored by the off-site copy);
    startup check that all protective triggers exist.

### P3: optional / later

11. Read-only auditor login for Z1 (Rz. 165, 174). The Z3 export usually suffices at this size.
12. Lock a year after the EÜR is filed.
13. Map categories to Anlage-EÜR lines (Rz. 97).
14. Persistent login log.
15. **E-invoices: issue** ZUGFeRD/Factur-X (EN 16931, PDF/A-3 with embedded XML, VAT category
    `E`, "Kleinunternehmer gemäß § 19 UStG"), validated before archiving. Not required:
    Kleinunternehmer are exempt from the issuing obligation (§ 34a UStDV), which otherwise applies
    from 2027 (> 800k turnover) or 2028. Useful because B2B customers increasingly expect it, and
    required once the business leaves § 19 (together with VAT support). Needs a spike: PDF/A-3
    from reportlab plus the `factur-x` library on the armv7 image.

## Retention

- Since 1.1.2025 (BEG IV) Buchungsbelege, including invoices, are kept 8 years (§ 147 Abs. 3 AO,
  § 14b UStG). Books and records (Aufzeichnungen) stay at 10 years.
- The tool keeps one uniform period, `INVOICES_RETENTION_YEARS`, default 10, as a deliberate
  conservative choice: the database rows are the records (Grundaufzeichnungen) belonging to the
  documents, documents and records stay together, the Verfahrensdokumentation and logs needed to
  understand them are covered, and one rule avoids mistakes about which document is which kind.
- The period starts at the end of the calendar year (§ 147 Abs. 4 AO): of the invoice date for
  invoices, of the upload for expense documents.
- `retain_until` is a minimum, not an end date: the retention period does not end while the
  documents still matter for taxes whose assessment period is open (§ 147 Abs. 3 AO). The tool
  never deletes anything, so nothing happens automatically on that date.

## Public repository

The GoBD do not require the code to be secret. Integrity comes from triggers, hashes, the hash
chain and off-site copies, not from obscurity, and a public git history supports proving
Programmidentität. Rules that follow:

- No business data in the repo: `config/sender.toml`, `data/`, `backups/`, `.env` stay ignored.
- The business-specific part of the Verfahrensdokumentation, the control log and deploy history
  live on the server or in a private repository, never here.
- Test fixtures stay synthetic.

## Status

| # | Item | State |
|---|---|---|
| 1 | Verfahrensdokumentation | public part 1–5 and template for the private part in `docs/verfahrensdokumentation/`; private part 6 to be filled in |
| 2 | Programmidentität | done |
| 3 | Complete change log | done |
| 4 | Z3 export | done; `index.xml` validated against the official DTD in the tests, a test import into IDEA is still open |
| 5 | Control log, restore test | done |
| 6 | Off-site backup | open, needs a decision |
| 7 | Receive e-invoices | done |
| 8 | Completeness and timeliness checks | done |
| 9 | Storno document | open, ask a Steuerberater |
| 10 | Hash chain, trigger check | done |
| 11–15 | P3 | open |

### Follow-up review (§ 34a UStDV, § 19 UStG, GoBD 2025)

| # | Item | State |
|---|---|---|
| 16 | § 19 note in the 2025 wording (§ 34a Nr. 5 UStDV) | done (`fix/small-business-note`); invoices created before keep the old sentence |
| 17 | Docs: GoBD second amendment (BMF 14.07.2025), retention since BEG IV | done (`gobd/legal-state-2025`); no code change needed |
| 18 | Reverse charge flag on expenses (§ 13b UStG) | done (`gobd/reverse-charge`) |
| 19 | Capital assets excluded from the expense total | done (`gobd/capital-assets`); register and AfA stay outside |
| 20 | Turnover limit monitor (§ 19 UStG since 2025) | done (`gobd/turnover-limit`) |
| 23 | § 13b in foreign currency: conversion hint | done (`gobd/rc-currency`) |
| 24 | § 13b hint: no false positive when the document charges VAT | done (`fix/reverse-charge-false-positive`) |
| 22 | External receipts for the § 19 limits | done (`gobd/external-receipts`) |
| 21 | Hotfix: cancelling a paid invoice erased the receipt | done (`fix/paid-cancellation`); paid → cancelled refused, lost receipts reported |
| 9 | Cancellation document | done (`gobd/cancellation`), see [CANCELLATION.md](CANCELLATION.md) |

## Questions for the Steuerberater

Found while implementing. "Open" means the code takes a documented position that has not been
confirmed; "decided" means the position is deliberate and needs no answer. Business-specific
questions are kept privately outside this repository.

### Open

1. **§ 13b reporting period.** Does the Finanzamt require Umsatzsteuer-Voranmeldungen for the § 13b
   tax of a Kleinunternehmer, or only the annual return? The tool shows quarterly and annual sums
   either way. (#18)
2. **§ 13b timing.** The tax arises with the end of the period in which the service was performed.
   The tool assigns a purchase to a quarter by its invoice date, as it does not record the service
   period. Is that acceptable, or does the service period need its own field? (#18)
3. **Assets between 250 € and 1,000 €.** The tool only knows "asset" or "immediately deductible"
   with the 800 € GWG limit. Should the Sammelposten option (§ 6 Abs. 2a EStG) be supported, and how
   should depreciable assets below the limit that are deliberately depreciated be marked? (#19)
4. **Asset hint on gross amounts.** The hint compares the gross amount with the 800 € net limit, so
   it also appears for net prices between about 673 € and 800 € (at 19 % VAT). Is a net amount field
   worth adding? (#19)
5. **Which activities count.** Receipts outside the tool count toward the Gesamtumsatz (see
   "Decided"), but whether a given activity is unternehmerisch at all, and which of its receipts
   therefore belong in the monitor, needs an answer per activity. (#22)
6. **Cancellation document required?** For an invoice the customer already received, is a separate
   cancellation document required for a Kleinunternehmer, or does the status plus reason suffice?
   Does the customer's bookkeeping prefer the title "Rechnungskorrektur" over "Stornorechnung"?
   (#9, CANCELLATION.md)
7. **Refunds and the Gesamtumsatz.** If a paid invoice is cancelled and the money refunded, does the
   refund reduce the § 19 turnover of the refund year, of the receipt year, or not at all? The tool
   subtracts nothing from the turnover and shows the refund separately in the cash overview, where
   it counts in the year it is paid out. (#9, #20, #21)

### Decided

- **Income outside the tool counts** (was question 5). For VAT a person has one Unternehmen covering
  all of their self-employed activities (§ 2 Abs. 1 Satz 2 UStG), so the Gesamtumsatz of § 19
  includes receipts not invoiced here. Implemented as append-only external receipts (#22); which
  activities belong in it stays open above.
- **Blocking on projected amounts stays** (was question 6). The monitor counts open invoices toward
  the limit and refuses a new § 19 invoice when the projection exceeds it. Once the limit is
  crossed, later receipts are taxable even for work done earlier, so an open § 19 invoice is a real
  risk; the exceptions are covered by the override, whose reason is logged. (#20)
- **Cancellation: same number sequence, title "Stornorechnung", document in `invoices`** with a
  negative amount and the refund tracked on it. (#9, CANCELLATION.md)

## Order

1. #3 logging fixes, #2 Programmidentität, #5 control log (shared schema foundation)
2. #4 export, #7 receiving e-invoices, #8 checks (parallel)
3. #10 hash chain
4. #1 Verfahrensdokumentation (public part), written against the finished system
5. #6 off-site backup (operations, needs a decision)
6. #9 after asking a Steuerberater, #11–15 later
