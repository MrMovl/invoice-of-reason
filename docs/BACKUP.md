# Backups

## What runs today

The `backup` service in `docker-compose.yml` runs `invoices backup` once at start and then every
24 hours. Each run:

1. Verifies every archived PDF and expense document against its stored SHA-256. If anything is off, the backup fails
   loudly instead of copying a damaged archive.
2. Takes a consistent SQLite snapshot with the online backup API (safe while the app runs).
3. Writes `backups/invoices-backup-YYYYMMDD-HHMMSS.tar.gz` containing `invoices.sqlite3`,
   `archive/**.pdf`, `expenses/**` (uploaded expense documents) and `manifest.json` (SHA-256 of
   every file).
4. Rotates: keeps the newest `INVOICES_BACKUP_KEEP` (default 30) plus the newest backup of every
   month, forever. At a few hundred KB per month this is negligible for decades.

Manual backup and download: the "Backups" page in the web UI.

Check logs: `docker compose logs backup`.

## Verify and restore

```sh
# check a backup file against its manifest
docker compose run --rm app invoices verify-backup /backups/invoices-backup-20260916-120000.tar.gz

# restore into an empty directory, then point the stack at it
docker compose down
mv data data.broken
docker compose run --rm -v "$PWD/data:/restore" app \
  invoices restore /backups/invoices-backup-20260916-120000.tar.gz /restore
docker compose up -d
docker compose exec app invoices verify
```

Without Docker, a backup is a plain tar.gz: `tar xzf file.tar.gz` gives you the database and PDFs.

## Off-site: not decided yet

Everything a copy needs is in `~/invoices/backups/` on the Pi (owned by uid 1000). Any of these
can be added later without touching the app:

| Option | Effort | Notes |
|--------|--------|-------|
| **restic** to Hetzner Storage Box / Backblaze B2 / S3 | cron + one config | Encrypted, deduplicated, versioned. Best fit for 10-year retention. Back up `~/invoices/data` directly or the tarballs. |
| **rclone** sync to a cloud drive (Proton Drive, pCloud, ...) | cron | Use `rclone copy` (not `sync`) so deletions never propagate. Encrypt with an rclone `crypt` remote. |
| Pull from the dev machine | cron/systemd timer on laptop | `rsync -a pi:invoices/backups/ ~/invoice-backups/`. Only works while the laptop is on. |
| USB disk on the Pi | cron | Protects against SD card death, not against theft/fire. |

Whichever is chosen: keep at least one copy outside the house, and test a restore once a year
(`invoices verify-backup` on the off-site copy).
