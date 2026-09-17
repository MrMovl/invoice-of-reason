# invoice-of-reason

Small self-hosted tool to create, track and archive invoices for a German Kleinunternehmer
(§ 19 UStG). Fill in a form, get a branded PDF, keep it tamper-evident for 10 years, back it up.
Upload received invoices and receipts as expenses; amounts are read from the PDF automatically.

- Flask + SQLite, PDFs rendered with reportlab, expense PDFs read with `pdftotext` (poppler)
- Runs as a Docker stack on a Raspberry Pi behind Cloudflare Tunnel
- UI in German

See [docs/PLAN.md](docs/PLAN.md) for architecture and decisions, [docs/BACKUP.md](docs/BACKUP.md)
for backups and restore, [docs/GOBD.md](docs/GOBD.md) for the GoBD compliance plan,
[docs/verfahrensdokumentation/](docs/verfahrensdokumentation/README.md) for the Verfahrensdokumentation (German).

## Disclaimer

**This is a personal project, built for one specific business. It is not tax or legal advice
and not a certified or audited bookkeeping product.**

- No tax advisor, auditor, tax authority or certification body has reviewed, tested or approved
  this software, its GoBD measures or its documentation. The GoBD themselves state that the tax
  authorities issue no certificates for software and that third-party certificates are not
  binding on them (Rz. 179–181).
- The GoBD analysis (`docs/GOBD.md`) and the Verfahrensdokumentation are the maintainer's own
  reading of the rules for their own situation. They may be incomplete, wrong or outdated, and
  they do not fit other businesses without review.
- Under the GoBD the taxpayer alone is responsible for the proper keeping of books and records
  and for the procedures used, even when software or third parties are involved (Rz. 21). If you
  use this project for your own invoices or bookkeeping, you do so entirely at your own risk.
  Have your setup checked by a tax advisor.
- The software is provided "as is", without warranty of any kind, as stated in the
  [LICENSE](LICENSE). The authors and contributors accept no liability for incorrect invoices,
  lost or unusable records, non-compliant bookkeeping, tax consequences or any other damage
  resulting from its use.

> **Haftungsausschluss:** Privates Projekt für einen einzelnen Betrieb, keine Steuer- oder
> Rechtsberatung. Software, GoBD-Umsetzung und Verfahrensdokumentation wurden von keiner
> Steuerberatung, Prüfstelle oder Finanzbehörde geprüft, testiert oder freigegeben. Für die
> Ordnungsmäßigkeit der Buchführung ist allein der Steuerpflichtige verantwortlich (GoBD Rz. 21).
> Nutzung auf eigenes Risiko; keine Gewährleistung und keine Haftung, siehe [LICENSE](LICENSE).

## Local development

Needs `pdftotext` for expense suggestions (`apt install poppler-utils`, `pacman -S poppler`).
Without it everything works, uploads just get no suggested values.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest

cp config/sender.example.toml config/sender.toml   # fill in real data, gitignored
export INVOICES_SECRET_KEY=$(.venv/bin/invoices secret-key)
export INVOICES_USERNAME=me
export INVOICES_PASSWORD_HASH=$(.venv/bin/invoices hash-password)
export INVOICES_INSECURE_COOKIES=1                 # plain http on localhost only
.venv/bin/flask --app 'invoices:create_app()' run --debug
```

Data goes to `./data`, backups to `./backups` (both gitignored).

## Deployment (Raspberry Pi)

First time only, on the Pi:

```sh
mkdir -p ~/invoices/config ~/invoices/data ~/invoices/backups
# copy config/sender.example.toml to ~/invoices/config/sender.toml and fill in real data
# create ~/invoices/.env from .env.example
```

Generate the `.env` values on the dev machine (no need to type the password on the server):

```sh
.venv/bin/invoices secret-key      # -> INVOICES_SECRET_KEY
.venv/bin/invoices hash-password   # -> INVOICES_PASSWORD_HASH (keep the single quotes in .env)
```

Then, from the dev machine:

```sh
./deploy.sh
```

It cross-builds for the Pi's architecture, runs the test suite inside that image, ships the image
over SSH, copies `docker-compose.yml` and starts the stack on `127.0.0.1:8082`.

### Public hostname

In Cloudflare Zero Trust, add a public hostname to the existing tunnel:
`invoices.example.com` -> `http://localhost:8082`.

Strongly recommended: also add a Cloudflare Access application for `invoices.example.com`
(policy: your email, one-time PIN). The app has its own login, Access adds a second layer.

## Operations

```sh
docker compose logs -f app
docker compose exec app invoices verify         # check all archived invoices and expense documents
docker compose run --rm app invoices backup     # extra backup now
docker compose run --rm app invoices restore-test /backups/<file>   # restore into a temp dir, verify, log it
```

### Importing an invoice issued before the program

Only for invoices that were created and sent before this tool existed (e.g. 2026-001). The PDF is
archived byte-for-byte with the same write-once rules as created invoices; the command shows all
values and any mismatch with the PDF text, and asks you to type the invoice number to confirm.

```sh
docker compose run --rm -v "$PWD/Rechnung_2026-001.pdf:/import/Rechnung_2026-001.pdf:ro" app \
  invoices import-invoice /import/Rechnung_2026-001.pdf \
  --number 2026-001 --issue-date 2026-09-02 --service-date 01.09.2026 --due-date 2026-09-16 \
  --customer-name "…" --customer-street "…" --customer-city "…" \
  --title "…" --amount 700,00 --reason "Vor Einführung des Programms erstellt und versandt"
docker compose run --rm app invoices export --year 2025   # CSV + index.xml export for a tax audit
```

Tax audit data export (GoBD Z3): see [docs/EXPORT.md](docs/EXPORT.md).

## License

Code: MIT. Fonts in `src/invoices/fonts` (PDF) and `src/invoices/static/fonts` (web UI): SIL Open Font
License (see the OFL files there).
