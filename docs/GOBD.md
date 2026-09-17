# GoBD compliance

State of this tool against the GoBD (BMF-Schreiben vom 28.11.2019, BStBl I S. 1269, geändert
durch BMF-Schreiben vom 11.03.2024, BStBl I S. 374, und vom 14.07.2025, BStBl 2025 I S. 1502,
anzuwenden ab 14.07.2025, Rz. 185). "Rz." refers to the Randziffern of the GoBD in that current
version. This is an engineering reading of the text, not tax advice: have a Steuerberater look at
the Verfahrensdokumentation and at the questions at the end.

Context: Kleinunternehmer (§ 19 UStG), Einnahmen-Überschuss-Rechnung. Rz. 15: the
requirements are judged "auch mit Blick auf die Unternehmensgröße". Proportionality applies, the
principles do not go away.

## Status

Numbers are stable; they are referenced from PRs, code comments and the questions below.

| # | Item | State |
|---|---|---|
| 1 | Verfahrensdokumentation | parts 1–5 in [verfahrensdokumentation/](verfahrensdokumentation/README.md); **open:** the private part 6 (template in the repo, content only on the server) |
| 2 | Programmidentität | done: version in the image and in `system_events`, deploys only from `main` |
| 3 | Complete change log | done: old → new values, reasons required, config changes logged |
| 4 | Z3 export | done: CSV + `index.xml` + DTD, see [EXPORT.md](EXPORT.md); **open:** a test import into IDEA |
| 5 | Control log, restore test | done: `control_runs`, `invoices restore-test` |
| 6 | Off-site backup | **open:** needs a provider decision, see [BACKUP.md](BACKUP.md) |
| 7 | Receive e-invoices | done: XRechnung XML and ZUGFeRD, kept unchanged |
| 8 | Completeness and timeliness checks | done: number gaps, 10-day review, category, payment method |
| 9 | Cancellation document | done: Stornorechnung, see [CANCELLATION.md](CANCELLATION.md) |
| 10 | Hash chain, trigger check | done: all logs chained, record states sealed, triggers restored and logged |
| 11 | Read-only auditor login (Z1) | **open, optional:** the Z3 export usually suffices at this size |
| 12 | Lock a year after the EÜR is filed | **open, optional** |
| 13 | Map categories to Anlage-EÜR lines (Rz. 97) | **open, optional** |
| 14 | Persistent login log | **open, optional** |
| 15 | Issue e-invoices (ZUGFeRD/Factur-X) | **open, optional:** Kleinunternehmer are exempt (§ 34a UStDV); needed once the business leaves § 19, together with VAT support. Needs a spike: PDF/A-3 from reportlab plus `factur-x` on armv7 |
| 16 | § 19 note in the 2025 wording (§ 34a Nr. 5 UStDV) | done; invoices created earlier keep the old sentence |
| 17 | GoBD as amended 14.07.2025, retention since BEG IV | done: documentation only, no code change needed |
| 18 | Reverse charge on expenses (§ 13b UStG) | done: flag, hints, quarterly sums |
| 19 | Capital assets out of the expense total | done: register and AfA stay outside the tool |
| 20 | Turnover limit monitor (§ 19 UStG since 2025) | done: status block, warnings, blocking with logged override |
| 21 | Hotfix: cancelling a paid invoice erased the receipt | done: refused, and older cases are reported |
| 22 | External receipts for the § 19 limits | done: append-only table, counted by the monitor |
| 23 | § 13b in foreign currency | done: hint pointing to the BMF average rate (§ 16 Abs. 6 UStG) |

## What is open

1. **Part 6 of the Verfahrensdokumentation (#1).** The business-specific part: business and
   responsibilities, other systems and the flow of documents (bank, email, paper), the
   Organisationsanweisung for scanning (Rz. 136), where the asset register and the records of
   activities outside this tool are kept, the off-site backup target and its key, and the
   organisational controls actually performed. Template:
   [6-betrieb-vorlage.md](verfahrensdokumentation/6-betrieb-vorlage.md); the filled-in version
   never belongs in this repository.
2. **Off-site backup (#6).** Everything a copy needs is in `backups/` on the server; the options
   are in [BACKUP.md](BACKUP.md). The decryption key has to stay available for the whole retention
   period (Rz. 134), a restore test belongs in the control log once a year, and a location outside
   the EU may need approval (§ 146 Abs. 2b AO).
3. **Test import of the export (#4).** `index.xml` is validated against the official DTD in the
   tests, but the ZIP has never been imported into the tax office's audit software (IDEA).
4. **Optional items #11–#15** as listed in the table.
5. **Questions for the Steuerberater** below.

## How the requirements are met

Short map from the GoBD to the implementation; details in the Verfahrensdokumentation.

| Requirement | Rz. | Where |
|---|---|---|
| Nachvollziehbarkeit, Belegfunktion | 30–35, 61–81 | unique numbers, document and booking data linked, history per record |
| Vollständigkeit, Einzelaufzeichnung | 36–43 | number gap analysis, duplicate detection by hash, nothing deletable |
| Zeitgerechtheit | 45–52 | 10-day review warning, recorded capture and booking dates |
| Unveränderbarkeit | 58–60, 107–112 | triggers, write-once files, SHA-256, change log with old → new, hash chain over all logs |
| IKS | 100–102 | automatic checks and the control log, see [5-iks.md](verfahrensdokumentation/5-iks.md) |
| Datensicherheit | 103–106 | access control, daily verified backup, restore test (off-site still open) |
| Aufbewahrung, Formate | 113–144 | received format kept unchanged, e-invoices whole, minimum retention per record, nothing deleted |
| Verfahrensdokumentation | 151–155 | parts 1–5 public, part 6 private |
| Datenzugriff Z1–Z3 | 158–178 | screen access, filtered analyses, ZIP export with `index.xml` and DTD |

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

## History

1. First pass (#1–#10, #16 excluded): change log, Programmidentität, control log, export, receiving
   e-invoices, checks, hash chain, Verfahrensdokumentation.
2. Review against § 34a UStDV, § 19 UStG and the GoBD 2025 (#16–#20).
3. Second review: hotfix #21, cancellation document #9, external receipts #22, currency hint #23.
