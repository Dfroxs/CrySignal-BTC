#!/usr/bin/env bash
# Off-site copy: pull the VPS's daily backups, the derivatives and news archives, the run
# manifests, the scoring-day outputs and the Claude cost ledger to this Mac.
#
# The VPS's own backups (deploy/backup_db.sh, 03:00 UTC) live on the same disk as the data.
# A dead disk or a lost host would take both, and the scanner's and Claude's forward-only
# opinions can never be regenerated. This keeps a second copy somewhere else.
#
#   bash scripts/pull_backups.sh          # by hand
#   launchd runs it twice a day: deploy/com.spotsignal.pullbackups.plist (install with
#   bash scripts/install_pull_backups.sh)
#
# After a verified pull it stamps data/backups/.offsite_ok on the VPS, so the hourly alarm
# there can say when the Mac has not pulled for a while. Exits 1 on any failure; no stamp then.
set -uo pipefail
HOST="${SPOTSIGNAL_HOST:-dmonk@45.151.155.178}"
REMOTE="playground/CrySignal-BTC/data"
DEST="${SPOTSIGNAL_BACKUP_DIR:-$HOME/Backups/SpotSignal}"
KEEP_DAYS=90                       # dated copies older than this go, except each month's 1st
SSH="ssh -o BatchMode=yes -o ConnectTimeout=20"
log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S') UTC  $*"; }

mkdir -p "$DEST/backups" "$DEST/derivs" "$DEST/scoring" "$DEST/manifests" "$DEST/news" "$DEST/ledger"
log "pull from $HOST"
FAIL=0
pull() {                           # $1 remote path, $2 local dir, rest: rsync filters
    local src="$1" dst="$2"; shift 2
    rsync -a --timeout=120 -e "$SSH" "$@" "$HOST:$REMOTE/$src" "$dst/" || { log "FAILED rsync $src"; FAIL=1; }
}
# No --delete: a copy the VPS has pruned (30 days) stays here until KEEP_DAYS.
pull "backups/" "$DEST/backups" --exclude=".offsite_ok"
pull "derivs/" "$DEST/derivs"
pull "scoring/" "$DEST/scoring"
pull "paper_run_manifest*.json" "$DEST/manifests"
pull "news/" "$DEST/news"                       # headlines + macro calendar (scripts/archive_news.py)
pull "llm_usage.jsonl" "$DEST/ledger"           # Claude cost ledger, append-only

# Verify the newest copy of every database before calling the pull good.
for prefix in db scanner scanner_hybrid scanner_random; do
    newest=$(ls -1t "$DEST"/backups/"$prefix"-20??????.db 2>/dev/null | head -1)
    [ -z "$newest" ] && continue
    if [ "$(sqlite3 "$newest" 'PRAGMA integrity_check;' 2>&1)" != "ok" ]; then
        log "CORRUPT $newest"; FAIL=1
    else
        log "ok $(basename "$newest")"
    fi
done

# Prune dated copies older than KEEP_DAYS, keeping the 1st of each month for good.
cutoff=$(date -v-"${KEEP_DAYS}"d +%Y%m%d)
for f in "$DEST"/backups/*-20??????.db; do
    [ -e "$f" ] || continue
    day=$(basename "$f" .db | grep -oE '20[0-9]{6}$')
    [ -n "$day" ] && [ "$day" \< "$cutoff" ] && [ "${day:6:2}" != "01" ] && rm -f "$f" && log "pruned $(basename "$f")"
done

if [ "$FAIL" -ne 0 ]; then
    log "pull FAILED; VPS not stamped"
    exit 1
fi
$SSH "$HOST" "date -u +%Y-%m-%dT%H:%M:%SZ > $REMOTE/backups/.offsite_ok" || { log "stamp failed"; exit 1; }
log "pull ok: $(du -sh "$DEST" | cut -f1) in $DEST"
