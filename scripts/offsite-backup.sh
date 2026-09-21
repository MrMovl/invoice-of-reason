#!/usr/bin/env bash
# Copy the finished backup tarballs off the server with restic (GoBD Rz. 103-106: the records
# have to survive the loss of the machine). Runs on the Pi, outside Docker, from cron or a timer.
#
# It copies ~/invoices/backups, never the live data directory: those tarballs were written only
# after the backup service verified every checksum and hash chain, each one is self-contained
# (database, invoice PDFs, expense documents, dokumentation/, manifest) and each one stays
# readable without this program. restic encrypts and deduplicates them client-side.
#
# Usage:
#   ./offsite-backup.sh                 # back up, apply retention, check the repository
#   ./offsite-backup.sh restore-test    # yearly control: fetch the newest tarball and test it
#   ./offsite-backup.sh snapshots       # list what is stored off-site
#   ./offsite-backup.sh init            # create the repository once
#
# Configuration lives in ~/.config/invoices-offsite.env (chmod 600), see docs/BACKUP.md:
#   RESTIC_REPOSITORY=sftp:storagebox:invoices
#   RESTIC_PASSWORD_FILE=/home/USER/.config/invoices-restic.pass
set -euo pipefail

CONFIG="${OFFSITE_ENV:-$HOME/.config/invoices-offsite.env}"
[ -f "$CONFIG" ] && . "$CONFIG"

INVOICES_DIR="${INVOICES_DIR:-$HOME/invoices}"
BACKUP_DIR="${BACKUP_DIR:-$INVOICES_DIR/backups}"
KEEP_DAILY="${KEEP_DAILY:-14}"
KEEP_MONTHLY="${KEEP_MONTHLY:-120}"   # ten years of monthly snapshots
# Read a thirtieth of the stored data per run, so all of it is read back once a month without
# pulling the whole repository every day.
READ_DATA_SUBSET="${READ_DATA_SUBSET:-1/30}"

command -v restic >/dev/null || { echo "restic not installed (apt install restic)" >&2; exit 1; }
: "${RESTIC_REPOSITORY:?set RESTIC_REPOSITORY in $CONFIG}"
: "${RESTIC_PASSWORD_FILE:?set RESTIC_PASSWORD_FILE in $CONFIG}"
export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE
# Losing this passphrase means losing every off-site copy for the rest of the ten-year retention.
# It belongs in part 6 of the Verfahrensdokumentation and on paper, not only on this machine.
[ -r "$RESTIC_PASSWORD_FILE" ] || { echo "cannot read $RESTIC_PASSWORD_FILE" >&2; exit 1; }

log() { echo ">> $(date '+%F %T') $*"; }

do_init() {
  restic init
  log "repository created: $RESTIC_REPOSITORY"
}

do_backup() {
  [ -d "$BACKUP_DIR" ] || { echo "no backup directory $BACKUP_DIR" >&2; exit 1; }
  # The backup service refuses to write a tarball when a checksum or chain is broken, so a stale
  # newest file means the local backups have been failing and nothing new is worth copying.
  newest=$(find "$BACKUP_DIR" -name 'invoices-backup-*.tar.gz' -mtime -2 -print -quit)
  [ -n "$newest" ] || echo "WARNING: no backup newer than two days in $BACKUP_DIR" >&2

  log "backing up $BACKUP_DIR"
  restic backup --tag invoices --host "$(hostname -s)" "$BACKUP_DIR"

  log "retention: $KEEP_DAILY daily, $KEEP_MONTHLY monthly"
  restic forget --tag invoices --keep-daily "$KEEP_DAILY" --keep-monthly "$KEEP_MONTHLY" --prune

  log "checking repository (data subset $READ_DATA_SUBSET)"
  restic check --read-data-subset="$READ_DATA_SUBSET"
  log "done"
}

# Yearly control (docs/BACKUP.md): restore the newest off-site tarball and let the program verify
# it against the running database. invoices restore-test records the result in control_runs.
do_restore_test() {
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  log "restoring the newest snapshot into $tmp"
  restic restore latest --tag invoices --target "$tmp"
  file=$(find "$tmp" -name 'invoices-backup-*.tar.gz' | sort | tail -1)
  [ -n "$file" ] || { echo "no tarball in the restored snapshot" >&2; exit 1; }
  log "testing ${file##*/}"
  (cd "$INVOICES_DIR" && docker compose run --rm -v "$tmp:/offsite:ro" app \
     invoices restore-test "/offsite${file#"$tmp"}")
}

case "${1:-backup}" in
  backup)       do_backup ;;
  init)         do_init ;;
  restore-test) do_restore_test ;;
  snapshots)    restic snapshots --tag invoices ;;
  *)            echo "usage: $0 [backup|init|restore-test|snapshots]" >&2; exit 1 ;;
esac
