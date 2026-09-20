# Backups

## What runs today

The `backup` service in `docker-compose.yml` runs `invoices backup` once at start and then every
24 hours. Each run:

1. Verifies every archived PDF and expense document against its stored SHA-256. If anything is off, the backup fails
   loudly instead of copying a damaged archive.
2. Takes a consistent SQLite snapshot with the online backup API (safe while the app runs).
3. Writes `backups/invoices-backup-YYYYMMDD-HHMMSS.tar.gz` containing `invoices.sqlite3`,
   `archive/**.pdf`, `expenses/**` (uploaded expense documents), `dokumentation/**` (see below) and
   `manifest.json` (SHA-256 of every file and the heads of the log hash chains). `restore-test` checks that the live database
   still contains those heads, which reveals removed recent log entries. Off-site copies of the
   manifests are what makes this an external anchor.
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

## Off-site copy: Hetzner Storage Box

Chosen target: a **Hetzner Storage Box BX11** (1 TB, data centre in Germany, SSH/SFTP/rsync,
Borg and restic, server-side snapshots, sub-accounts). Ten years of backups stay well under a
gigabyte, so the smallest box is far more than enough; it was picked for the location, the
snapshots and the price, not the space.

`scripts/offsite-backup.sh` runs on the Pi and pushes `~/invoices/backups` there with restic:
encrypted and deduplicated client-side, so Hetzner never sees the invoices. It copies the
finished tarballs, not the live data directory – each tarball was written only after every
checksum and hash chain verified, and each one is readable without this program.

### Setup

1. Order the box, create a **sub-account** for the Pi with its own directory, and enable SSH
   plus an automatic snapshot plan (daily, keep 7). The snapshots are the protection that
   restic cannot give: they survive a compromised Pi deleting or rewriting the remote data.
2. On the Pi, give the box an SSH alias, because restic's sftp URL has no place for port 23:

   ```sh
   # ~/.ssh/config
   Host storagebox
       HostName uXXXXXX.your-storagebox.de
       User uXXXXXX-sub1
       Port 23
       IdentityFile ~/.ssh/id_ed25519
   ```

   ```sh
   ssh-copy-id -s -p 23 uXXXXXX-sub1@uXXXXXX.your-storagebox.de   # -s: restricted shell
   ssh storagebox ls                                              # must work without a password
   ```
3. Write the configuration and the repository passphrase:

   ```sh
   install -m 600 /dev/null ~/.config/invoices-restic.pass
   openssl rand -base64 32 > ~/.config/invoices-restic.pass
   install -m 600 /dev/null ~/.config/invoices-offsite.env
   cat > ~/.config/invoices-offsite.env <<'EOF'
   RESTIC_REPOSITORY=sftp:storagebox:invoices
   RESTIC_PASSWORD_FILE=/home/USER/.config/invoices-restic.pass
   EOF
   ```

   **The passphrase is part of the records.** Without it every off-site copy is unreadable for
   the rest of the ten-year retention period. It goes on paper and into part 6 of the
   Verfahrensdokumentation, not only onto the Pi, which is the machine the copy exists for.
4. Copy the script to the Pi (`deploy.sh` ships only the image and the compose file, so the
   repository is not on the server), initialise the repository and run it once:

   ```sh
   scp scripts/offsite-backup.sh pi:invoices/offsite-backup.sh    # from the dev machine
   ssh pi 'sudo apt install -y restic && invoices/offsite-backup.sh init && invoices/offsite-backup.sh'
   ```
5. Run it daily, an hour after the backup service:

   ```sh
   crontab -e   # 40 3 * * * $HOME/invoices/offsite-backup.sh >> $HOME/invoices/offsite.log 2>&1
   ```

   Re-copy the script after changing it in the repository; it is the one file of this project
   that lives on the Pi outside Docker.

Retention off-site: 14 daily snapshots plus one per month for 120 months. Because restic
deduplicates, keeping ten years of monthly snapshots costs almost nothing beyond the tarballs
themselves. Every run also verifies the repository and reads back a thirtieth of the stored
data, so all of it is re-read once a month.

### Yearly control

```sh
scripts/offsite-backup.sh restore-test
```

It pulls the newest tarball from the Storage Box, restores it into a temporary directory and
lets `invoices restore-test` verify every document against the restored database and compare the
log hash chain heads with the live database. The result is written to the control log. Testing
the off-site copy – not a local file – is the point: it proves the copy that would actually be
used is intact.

### Other targets

The script is restic, so nothing is tied to Hetzner: any restic backend (Backblaze B2, S3, a
second box) works by changing `RESTIC_REPOSITORY`. For a plain cloud drive instead, `rclone copy`
(never `sync`, so deletions never propagate) through an rclone `crypt` remote does the same job.
Whatever it is, keep at least one copy outside the house and test the restore once a year.

## Documents in the backup

`data/dokumentation/` is meant for documents that belong to the records but are not created by the
program, above all the business-specific part 6 of the Verfahrensdokumentation (the parts 1–5 are
in this repository). Everything in it is copied into every backup with its checksum and restored as
a writable file.

It needs no repository, but changes must stay traceable (GoBD Rz. 154): write a new dated file per
version (`teil6-2026-09-17.md`), never edit an old one, and keep the change table in the document.
The 30 daily backups plus one per month, kept forever, are the second record of that history.

## Control log

Every `verify`, backup (automatic, manual, failed) and `restore-test` run is written to the
append-only `control_runs` table and listed on the Backups page (GoBD Rz. 100: controls are
performed and logged).
