# GoBD compliance plan

Gap analysis of this tool against the GoBD (BMF-Schreiben vom 28.11.2019, BStBl I S. 1269, geändert
durch BMF-Schreiben vom 11.03.2024). "Rz." refers to the Randziffern of the GoBD. This is an
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

7. **E-invoices: receive** (Rz. 125, 131). Receiving B2B e-invoices is mandatory since 1.1.2025.
   Accept XRechnung/UBL/CII XML uploads, store them unchanged, read booking suggestions from the
   XML, show a readable view. ZUGFeRD/Factur-X PDFs keep their embedded XML already (no
   conversion); read suggestions from it too. Reading ZUGFeRD is groundwork for issuing it (#15).
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

## Public repository

The GoBD do not require the code to be secret. Integrity comes from triggers, hashes, the hash
chain and off-site copies, not from obscurity, and a public git history supports proving
Programmidentität. Rules that follow:

- No business data in the repo: `config/sender.toml`, `data/`, `backups/`, `.env` stay ignored.
- The business-specific part of the Verfahrensdokumentation, the control log and deploy history
  live on the server or in a private repository, never here.
- Test fixtures stay synthetic.

## Order

1. #3 logging fixes, #2 Programmidentität, #5 control log (shared schema foundation)
2. #4 export, #7 receiving e-invoices, #8 checks (parallel)
3. #10 hash chain
4. #1 Verfahrensdokumentation (public part), written against the finished system
5. #6 off-site backup (operations, needs a decision)
6. #9 after asking a Steuerberater, #11–15 later
