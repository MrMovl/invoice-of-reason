# invoice-of-reason

Small self-hosted tool to create, track and archive invoices for a German Kleinunternehmer
(§ 19 UStG). Fill in a form, get a branded PDF, keep it tamper-evident for 10 years, back it up.

- Flask + SQLite, PDFs rendered with reportlab
- Runs as a Docker stack on a Raspberry Pi behind Cloudflare Tunnel
- UI in German

See [docs/PLAN.md](docs/PLAN.md) for architecture and decisions, [docs/BACKUP.md](docs/BACKUP.md)
for backups and restore.

## Local development

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
docker compose exec app invoices verify         # check all archived PDFs
docker compose run --rm app invoices backup     # extra backup now
```

## License

Code: MIT. Fonts in `src/invoices/fonts`: SIL Open Font License (see the OFL files there).
