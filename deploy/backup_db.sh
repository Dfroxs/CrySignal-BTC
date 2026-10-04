#!/usr/bin/env bash
# Daily snapshot of the paper-run database.
#
# Uses sqlite3 .backup, NOT cp. The bot writes to this file every hour, and cp of a
# live SQLite database can capture a torn write - a half-finished transaction that
# restores as a corrupt file, which you discover only when you need it. .backup takes
# a consistent snapshot of a database that is being written to.
#
# The server can be rebuilt in 15 minutes. Weeks of paper-run rows cannot.
set -euo pipefail
SRC="$HOME/playground/CrySignal-BTC/data/signal_history.db"
DIR="$HOME/playground/CrySignal-BTC/data/backups"
mkdir -p "$DIR"
OUT="$DIR/db-$(date -u +%Y%m%d).db"
sqlite3 "$SRC" ".backup '$OUT'"
sqlite3 "$OUT" "PRAGMA integrity_check;" | grep -qx ok || { echo "BACKUP CORRUPT: $OUT" >&2; exit 1; }
# keep 30 days
ls -1t "$DIR"/db-*.db 2>/dev/null | tail -n +31 | xargs -r rm --
