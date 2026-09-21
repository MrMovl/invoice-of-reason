# invoice-of-reason

Small self-hosted tool to create, track and archive invoices for a German Kleinunternehmer
(§ 19 UStG). Fill in a form, get a branded PDF, keep it tamper-evident for 10 years, back it up.
Upload received invoices and receipts as expenses, including e-invoices (XRechnung, ZUGFeRD);
amounts are read from the document automatically. Cancel a sent invoice with a Stornorechnung,
mark purchases under § 13b UStG and capital assets, and watch the § 19 turnover limits.

- Flask + SQLite, PDFs rendered with reportlab, expense PDFs read with `pdftotext` (poppler)
- Runs as a Docker stack on a Raspberry Pi behind Cloudflare Tunnel
- UI in German

See [docs/PLAN.md](docs/PLAN.md) for architecture and decisions, [docs/BACKUP.md](docs/BACKUP.md)
for backups and restore, [docs/GOBD.md](docs/GOBD.md) for the GoBD compliance plan,
[docs/verfahrensdokumentation/](docs/verfahrensdokumentation/README.md) for the Verfahrensdokumentation (German),
[docs/EXPORT.md](docs/EXPORT.md) for the tax-audit export and
[docs/CANCELLATION.md](docs/CANCELLATION.md) for how cancellations work.

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

Data goes to `./data`, backups to `./backups` (both gitignored). `./data/dokumentation` is for
documents that belong to the records but are not created by the program (part 6 of the
Verfahrensdokumentation); every backup includes it.

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

Deploy only from `main` after the PR is merged. `scripts/deploy-check.sh` refuses uncommitted
changes and any commit that is not on `origin/main`, because the app records the deployed commit
and it must stay in git history (GoBD Programmidentität). The repository enforces the rest: PRs
can only be merged with merge commits (no squash or rebase), merged branches are deleted so stacked
PRs move onto `main`, and `main` rejects force pushes and deletion. Emergency builds with
`ALLOW_DIRTY=1` or `ALLOW_UNMERGED=1` are marked `-dirty` / `-unmerged` in the recorded version.

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

### Read-only report over SSH

`invoices report --json [--year JJJJ]` prints the business figures of one year (default: the
current one) as JSON: invoices and cancellation documents (number, date, customer, title, amount,
status, payment date, cancellation link), income, refunds and expenses per month by payment date
with the surplus before AfA, the § 19 UStG figures (received, open, recorded outside the tool,
applicable limit, headroom) and the share of the largest customer. It opens the database with
SQLite `mode=ro`: no writes, no migrations, no log entries. It never prints sender data, settings,
paths, hosts or versions; any failure is one generic line on stderr and a non-zero exit code.

The `report` service runs it every hour and writes `report.json` into the report directory,
replacing the file only with a complete new report; `generated_at` in the file shows its age. It
gets no login data, no sender data and no network. Another machine fetches the file over SSH as
a separate system user that can read this one file and nothing else: no access to the stack, the
data directory or Docker.

One-time setup on the server (placeholders in capitals):

```sh
# report directory outside the home directory, writable by the container user (uid 1000)
sudo install -d -o 1000 -g 1000 -m 755 /srv/invoices-report
echo 'INVOICES_REPORT_DIR=/srv/invoices-report' >> ~/invoices/.env

# system user without password; its authorized_keys belong to root, so the user cannot change them
sudo useradd --system --create-home --home-dir /var/lib/invoices-report --shell /bin/sh invoices-report
sudo install -d -o root -g root -m 755 /var/lib/invoices-report/.ssh
sudo install -o root -g root -m 644 /dev/null /var/lib/invoices-report/.ssh/authorized_keys
sudoedit /var/lib/invoices-report/.ssh/authorized_keys
```

The one line in that `authorized_keys` pins the key to reading the file:

```
command="cat /srv/invoices-report/report.json 2>/dev/null || { echo 'FEHLER: Bericht nicht verfügbar.' >&2; exit 1; }",restrict,from="203.0.113.10" ssh-ed25519 AAAA...PLACEHOLDER report-client
```

- `restrict` turns off forwarding, the PTY and `~/.ssh/rc`; the forced command ignores whatever
  the client asks to run.
- `from=` limits the key to the client's address; drop it if that address is not fixed.
- If `sshd_config` has `AllowUsers` or `AllowGroups`, add the user there.
- Then deploy (or `docker compose up -d`) and check that `/srv/invoices-report/report.json`
  appears.

From the client:

```sh
ssh -i ~/.ssh/report_key invoices-report@SERVER > report.json
```

## License

Code: MIT. Fonts in `src/invoices/fonts` (PDF) and `src/invoices/static/fonts` (web UI): SIL Open Font
License (see the OFL files there).
