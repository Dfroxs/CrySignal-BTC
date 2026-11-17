#!/usr/bin/env bash
# Daily snapshot of the paper-run databases: the bot's, and each scanner arm's.
#
# Uses sqlite3 .backup, NOT cp. The bot writes to this file every hour, and cp of a
# live SQLite database can capture a torn write - a half-finished transaction that
# restores as a corrupt file, which you discover only when you need it. .backup takes
# a consistent snapshot of a database that is being written to.
#
# The server can be rebuilt in 15 minutes. Weeks of paper-run rows cannot - and the
# scanner's forward-only opinions (H-SC, H-SC2) can never be regenerated at all.
set -euo pipefail
DATA="$HOME/playground/CrySignal-BTC/data"
DIR="$DATA/backups"
mkdir -p "$DIR"
DAY=$(date -u +%Y%m%d)
FAIL=0
backup() {                                  # $1 source file, $2 prefix of the copy
    local out="$DIR/$2-$DAY.db"
    sqlite3 "$1" ".backup '$out'"
    sqlite3 "$out" "PRAGMA integrity_check;" | grep -qx ok || { echo "BACKUP CORRUPT: $out" >&2; FAIL=1; }
    # keep 30 days per prefix ("scanner-" never matches "scanner_hybrid-")
    ls -1t "$DIR"/"$2"-*.db 2>/dev/null | tail -n +31 | xargs -r rm --
}
backup "$DATA/signal_history.db" db         # name kept: morning_check.sh looks for db-<day>.db
for name in scanner scanner_hybrid scanner_random; do
    [ -f "$DATA/$name.db" ] && backup "$DATA/$name.db" "$name"
done
exit $FAIL
