#!/usr/bin/env bash
# Off-site copy: pull everything the VPS produces that is not code (audit 2026-11-19): daily
# database backups, the derivatives and news archives, run manifests, scoring-day outputs,
# the Claude cost ledger, the bot's state files and every log. Only .env stays behind.
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
#
# It is also the watchdog for the watchdog (2026-11-19). The VPS's alarm cannot report the VPS
# dying. So from here, twice a day, it checks that the VPS answers, that the bot ran a cycle in
# the last 3 h, and that the alarm wrote its heartbeat (data/alarm.ok) in the last 3 h. If not,
# it raises a macOS notification and sends Telegram from this Mac, with the token read from the
# repo's .env and never printed.
set -uo pipefail
HOST="${SPOTSIGNAL_HOST:-dmonk@45.151.155.178}"
REMOTE="playground/CrySignal-BTC/data"
DEST="${SPOTSIGNAL_BACKUP_DIR:-$HOME/Backups/SpotSignal}"
KEEP_DAYS=90                       # dated copies older than this go, except each month's 1st
SSH="ssh -o BatchMode=yes -o ConnectTimeout=20"
log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S') UTC  $*"; }
REPO="$(cd "$(dirname "$0")/.." && pwd)"
envval() { grep -E "^$1=" "$REPO/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'" ; }
notify() {                          # macOS notification + Telegram from this Mac
    log "WATCHDOG: $1"
    osascript -e "display notification \"$1\" with title \"SpotSignal: penjaga Mac\"" >/dev/null 2>&1 || true
    local tok chat; tok=$(envval TELEGRAM_BOT_TOKEN); chat=$(envval TELEGRAM_CHAT_ID)
    if [ -n "$tok" ] && [ -n "$chat" ]; then
        curl -s -m 20 -o /dev/null "https://api.telegram.org/bot$tok/sendMessage" \
             --data-urlencode "chat_id=$chat" --data-urlencode "text=🛰️ Penjaga di Mac: $1" || log "telegram send failed"
    fi
}
if ! $SSH "$HOST" true 2>/dev/null; then
    notify "VPS tidak bisa dihubungi. Bot, alarm, dan notif Telegram dari VPS kemungkinan mati semua."
    exit 1
fi
# Bot cycle age and alarm heartbeat age, in minutes ("-" when the heartbeat does not exist yet).
AGES=$($SSH "$HOST" "cd $REMOTE/.. && python3 -c \"
import os, sqlite3, time, datetime as d
c = sqlite3.connect('file:data/signal_history.db?mode=ro', uri=True)
t = c.execute(\\\"SELECT MAX(substr(replace(timestamp,'T',' '),1,19)) FROM cycle_log\\\").fetchone()[0]
bot = (time.time() - d.datetime.fromisoformat(t).replace(tzinfo=d.timezone.utc).timestamp()) / 60 if t else -1
a = 'data/alarm.ok'
alarm = (time.time() - os.path.getmtime(a)) / 60 if os.path.exists(a) else '-'
print(int(bot), alarm if alarm == '-' else int(alarm))
\"" 2>/dev/null)
read -r BOT_MIN ALARM_MIN <<< "$AGES"
if [ -z "$BOT_MIN" ] || [ "$BOT_MIN" -lt 0 ] || [ "$BOT_MIN" -gt 180 ]; then
    notify "Bot BTC tidak menjalankan siklus: terakhir ${BOT_MIN:-?} menit lalu (batas 180)."
fi
if [ "$ALARM_MIN" != "-" ] && [ -n "$ALARM_MIN" ] && [ "$ALARM_MIN" -gt 180 ]; then
    notify "Alarm di VPS tidak jalan ${ALARM_MIN} menit (cron mati?). Masalah lain di VPS tidak akan dilaporkan."
fi
log "watchdog: bot cycle ${BOT_MIN} min ago, alarm heartbeat ${ALARM_MIN} min ago"

mkdir -p "$DEST/backups" "$DEST/derivs" "$DEST/scoring" "$DEST/manifests" "$DEST/news" "$DEST/ledger" \
         "$DEST/state" "$DEST/logs"
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
# Audit 2026-11-19, so nothing that changes on the host is left uncopied:
# - state: the adaptive thresholds' rolling logs, alarm/Q&A state, caches (a rebuilt host
#   resumes from them instead of from zero);
# - logs: the bot's stdout and INFO log, scanner, alarm, report, scoring, archive logs.
# .env is deliberately NOT copied: it holds secrets; the owner keeps it.
pull "*.json" "$DEST/state" --exclude="paper_run_manifest*.json"
pull "*.log" "$DEST/logs"
pull "signal_history.csv" "$DEST/state"
rsync -a --timeout=120 -e "$SSH" "$HOST:playground/CrySignal-BTC/paper_run.log" \
      "$HOST:playground/CrySignal-BTC/spotsignal.log" "$DEST/logs/" || { log "FAILED rsync bot logs"; FAIL=1; }

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
    notify "Backup ke Mac gagal (rsync atau cek integritas). Lihat ~/Backups/SpotSignal/pull.log."
    log "pull FAILED; VPS not stamped"
    exit 1
fi
$SSH "$HOST" "date -u +%Y-%m-%dT%H:%M:%SZ > $REMOTE/backups/.offsite_ok" || { log "stamp failed"; exit 1; }
log "pull ok: $(du -sh "$DEST" | cut -f1) in $DEST"
