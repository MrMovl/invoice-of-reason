# Cancellation document: design proposal

Status: **proposal, not implemented.** Implements docs/GOBD.md #9 once approved. Questions that
need the Steuerberater are marked **(StB)** and collected in docs/GOBD.md.

## Problem

Today "Stornieren" is a status change to `cancelled` with a mandatory reason. That is enough for an
invoice that never left the house. For an invoice the customer already received it is not: the
customer needs a document that clearly cancels the original, and an auditor will ask what the
customer received (GoBD Rz. 64: corrections must refer back to the original record).

A second problem found while writing this: `set_status(…, "cancelled")` clears `paid_date` and
`payment_method`. Cancelling a paid invoice therefore removes a real receipt from the cash
overview and from the § 19 turnover monitor, even retroactively for a past year. A receipt is a
fact; undoing it needs a refund, not an edit.

## 1. Never sent vs. already sent

Yes, distinguish them, and make the user state it at the moment of cancelling. The tool does not
track sending, so it cannot decide.

| Case | Action | Result |
|---|---|---|
| **Never sent** (drafted in error, wrong customer, noticed before sending) | "Stornieren – wurde nicht versandt", reason required | Today's behaviour: status `cancelled`, event with reason and the statement "nicht versandt". No document. |
| **Already sent** | "Stornorechnung erstellen", reason required | A cancellation document is created, archived and linked; the original gets status `cancelled`. |

The "never sent" path is only allowed while the invoice has no payment. A paid invoice has by
definition reached the customer. Both statements are logged, so a wrong choice is traceable.

## 2. Numbering

**Proposal: the next number of the same sequence** (`2026-007` cancels `2026-003`).

- Every document the customer receives gets a unique number from one gap-free sequence
  (Rz. 40, 50); an auditor sees the cancellation in its chronological place.
- `number_gaps` and `number_problem` stay unchanged: a cancellation document is a regular number.
  A separate sequence (e.g. `S2026-001`) would need its own gap analysis and a second scheme in
  `SCHEME_RE`.
- The "Abweichende Nummer" override applies unchanged if someone deviates.

Whether a separate sequence is preferred for the customer's bookkeeping is a matter of taste, not
of law as far as I can tell. **(StB)**

## 3. Storage

**Proposal: a row in `invoices`**, not a separate table, because a cancellation document is an
outgoing document with number, PDF, hash, retention and history exactly like an invoice.

New columns (migration, defaults keep all record hashes valid):

| Column | Definition | Meaning |
|---|---|---|
| `kind` | `TEXT NOT NULL DEFAULT '' CHECK (kind IN ('', 'cancellation'))` | '' = invoice, `cancellation` = cancellation document |
| `cancels_invoice_id` | `INTEGER REFERENCES invoices(id)`, default NULL | the original; NULL for invoices |

Plus a unique partial index on `cancels_invoice_id` (one cancellation per original) and a CHECK-like
trigger that `cancels_invoice_id` is set exactly when `kind = 'cancellation'` and points to an
invoice of kind ''.

**Immutability.** Both columns are identity fields: the migration drops and recreates
`invoices_immutable` with `kind` and `cancels_invoice_id` added to its column list (same trigger
name, so `expected_triggers` and the startup trigger check keep working). The no-delete trigger
already covers the new rows. The PDF is written once like any invoice and never regenerated.

**Amounts.** `amount_cents` of the cancellation row holds the original amount as a **negative**
value (−70000). The column has no positivity CHECK; `parse_amount` stays positive-only and is not
used for this row, the value is copied from the original. A negative amount on the row makes every
sum that forgets to filter by `kind` visibly wrong instead of silently double-counting.

**Status of the cancellation row** describes the refund:

| Original | Cancellation row status | Meaning |
|---|---|---|
| not paid | `cancelled` | nothing to refund |
| paid | `open` | refund due |
| paid | `paid`, `paid_date` = refund date, `payment_method` | refunded |

**Paid original.** Its `paid_date` and `payment_method` stay; only the status becomes `cancelled`.
`set_status` gets a guard: a paid invoice can no longer be set to `cancelled` directly, only via
the cancellation document. Re-opening a cancelled original is refused once a cancellation document
exists.

**Hash chain.** Creating the document writes two events in one transaction: `created` on the new
row (detail: `sha256=…; Storno zu Rechnung 2026-003 vom 16.09.2026; Grund: „…“`) and
`status:cancelled` on the original (detail as today plus `Stornorechnung 2026-007`). Each carries
its record's state hash. Refund recording is a normal `status:paid` event on the cancellation row.

**cash_summary.** Income = receipts of invoices (`kind = ''`, `paid_date` in the year, status
`paid` **or** `cancelled`) minus refunds (`kind = 'cancellation'`, status `paid`, `paid_date` in the
year). A cancellation never counts as income; a refund reduces income in the year it is paid out
(Abflussprinzip), and the original receipt stays in its own year. Whether a refund is better shown
as a separate line than as negative income is a presentation choice; proposal: separate
"Erstattungen" line, surplus computed from both.

**Turnover monitor.** Receipts as above. Whether refunds reduce the Gesamtumsatz of the refund
year, or the receipt year, is open. **(StB)** Until clarified, the monitor subtracts nothing and
says so (safe side).

**Lists and totals.** The invoice list shows cancellation rows with a "Storno" badge and negative
amount; "Umsatz" and "Offen" exclude `kind = 'cancellation'`; a separate "Erstattung offen" figure
shows refunds due.

**Export.** Both columns appear automatically (schema-driven). `index.xml` gets a `ForeignKey`
`invoices.cancels_invoice_id → invoices`; descriptions and the README explain `kind`, the negative
amount and the refund status. Validation against the DTD stays in the tests.

**Import.** `import-invoice` keeps importing invoices only. A cancellation document issued before
the tool existed would need `--kind cancellation --cancels 2026-00X`; not proposed unless needed.

## 4. PDF content

Rendered by the existing layout with a small variant; the fields follow the invoice, which is built
for § 34a UStDV:

- Title: **"Stornorechnung"**. Not "Gutschrift", which in VAT law means self-billing by the
  recipient (§ 14 Abs. 2 UStG). "Rechnungskorrektur" is the alternative. **(StB)**
- Reference line directly under the title: "Storno der Rechnung Nr. 2026-003 vom 16.09.2026".
- Sender block and footer as on invoices: name, address, Steuernummer (§ 34a UStDV).
- Customer name and address copied from the original row (not from current input).
- Date of issue (today) and the original Leistungsdatum/-zeitraum.
- Position: "Storno: <original title>", amount negative (−700,00 €), Gesamtbetrag negative.
- The § 19 note in the wording printed on the original (from its `payload_json.texts`, falling back
  to the current constant), so original and cancellation agree.
- Payment block instead of bank details: unpaid original "Die Rechnung ist damit gegenstandslos."
  / paid original "Der Betrag von 700,00 € wird erstattet."
- `payload_json` of the cancellation row records the reference, the texts printed and the sender
  snapshot, as for invoices.

## 5. Linking in UI and logs

- Original detail page: status "Storniert durch 2026-007" with link; the cancel form offers the two
  paths from section 1.
- Cancellation detail page: "Storno der Rechnung 2026-003" with link, refund status and
  "Erstattung erfassen" (date, method) when the original was paid.
- Both event logs name the other document's number (section 3). The `invoices.cancels_invoice_id`
  column carries the link for the export.

## Out of scope

- Partial corrections (changing the amount): cancel fully and issue a new invoice.
- Existing cancelled invoices keep their status; they are treated as "never sent" cases. If one of
  them was in fact sent, a cancellation document can be created for it later (allowed for status
  `cancelled` without a cancellation row).
- The already existing loss of `paid_date` on cancelled paid invoices cannot be undone for data
  created before the change; the events still show the old payment date.

## Implementation outline (after approval)

1. Migration: `kind`, `cancels_invoice_id`, unique index, consistency trigger, recreated
   `invoices_immutable`; test on a pre-migration database with `invoices verify`.
2. `archive.cancel_invoice(conn, archive_dir, invoice_id, reason, sent: bool, sender, …)`; guard in
   `set_status`; refund via `set_status` on the cancellation row.
3. PDF variant, detail pages, list badges and totals, cash_summary and turnover changes.
4. Export descriptions and foreign key, Verfahrensdokumentation 2.3, 3.2, 5 and the migration
   table, GOBD.md status.
