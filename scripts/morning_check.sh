#!/usr/bin/env bash
# Five-minute morning check for both bots on the VPS.
#
# Written because "check it tomorrow" is not something a chat session can promise: the
# session does not survive the night, and a scheduled cloud agent has no SSH key to this
# host. One command instead.
#
#   bash scripts/morning_check.sh
#
# Reports and exits 1 if anything needs attention, so it can be cron'd later.
set -uo pipefail
HOST="${SPOTSIGNAL_HOST:-dmonk@45.151.155.178}"
SINCE="${1:-24 hours ago}"
PROBLEMS=0
note() { printf "  %-34s %s\n" "$1" "$2"; }
bad()  { printf "  %-34s %s  <-- PERIKSA\n" "$1" "$2"; PROBLEMS=$((PROBLEMS+1)); }

echo "=== $(date -u '+%Y-%m-%d %H:%M UTC') — morning check ($HOST) ==="

OUT=$(ssh -o ConnectTimeout=25 -o BatchMode=yes "$HOST" bash -s <<'REMOTE'
cd ~/playground/CrySignal-BTC || exit 9
DB=data/signal_history.db
echo "boot|$(uptime -s)"
echo "spot_svc|$(systemctl is-active spotsignal)|$(systemctl show spotsignal -p NRestarts --value)"
echo "alloc_svc|$(systemctl --user is-active nakhoda-alloc)|$(systemctl --user show nakhoda-alloc -p NRestarts --value)"
# The contributions check must start at the RUN's start, not 24h ago: rows written by
# the previous run are legitimately NULL and would read as a fault every morning for a
# day after any restart. The manifest exists to mark exactly this boundary.
RUNSTART=$(python3 -c "import json;print(json.load(open('data/paper_run_manifest.json'))['started_at'][:19].replace('T',' '))" 2>/dev/null || echo "1970-01-01 00:00:00")
echo "runstart|$RUNSTART"
echo "cycles|$(sqlite3 "$DB" "SELECT COUNT(*) FROM cycle_log WHERE timestamp >= datetime('now','-24 hours');")"
echo "nullcontrib|$(sqlite3 "$DB" "SELECT COUNT(*) FROM cycle_log WHERE timestamp >= '$RUNSTART' AND contributions IS NULL;")"
echo "runcycles|$(sqlite3 "$DB" "SELECT COUNT(*) FROM cycle_log WHERE timestamp >= '$RUNSTART';")"
echo "lastcycle|$(sqlite3 "$DB" "SELECT MAX(timestamp) FROM cycle_log;")"
echo "openpos|$(sqlite3 "$DB" "SELECT COUNT(*) FROM paper_positions WHERE closed_at IS NULL;")"
echo "newpos|$(sqlite3 "$DB" "SELECT COUNT(*) FROM paper_positions WHERE opened_at >= datetime('now','-24 hours');")"
echo "blindfut|$(sqlite3 "$DB" "SELECT COUNT(*) FROM cycle_log WHERE mode='futures' AND funding_rate=0;")"
echo "spoterr|$(grep -ciE 'error|traceback|exception' <(tail -400 paper_run.log))"
echo "backup|$(ls -1t data/backups/db-2026*.db 2>/dev/null | head -1 | xargs -r basename)"
echo "allocerr|$(journalctl --user -u nakhoda-alloc --no-pager -S '24 hours ago' 2>/dev/null | grep -ciE 'error|traceback|consecutive')"
echo "allocact|$(journalctl --user -u nakhoda-alloc --no-pager -S '24 hours ago' 2>/dev/null | grep -cE 'Rebalance|already at target')"
echo "allocdust|$(journalctl --user -u nakhoda-alloc --no-pager -S '24 hours ago' 2>/dev/null | grep -c 'leaving it as dust')"
REMOTE
) || { echo "  TIDAK BISA SSH KE $HOST"; exit 1; }

g() { echo "$OUT" | grep "^$1|" | cut -d'|' -f2; }
h() { echo "$OUT" | grep "^$1|" | cut -d'|' -f3; }

echo "--- mesin ---"; note "boot terakhir" "$(g boot)"
echo "--- layanan ---"
[ "$(g spot_svc)"  = active ] && note "spotsignal"    "active (restart: $(h spot_svc))"  || bad "spotsignal"    "$(g spot_svc)"
[ "$(g alloc_svc)" = active ] && note "nakhoda-alloc" "active (restart: $(h alloc_svc))" || bad "nakhoda-alloc" "$(g alloc_svc)"

echo "--- SpotSignal (run 2) ---"
C=$(g cycles); N=$(g nullcontrib)
[ "${C:-0}" -gt 0 ] && note "siklus 24 jam" "$C" || bad "siklus 24 jam" "0 — bot diam"
note "siklus sejak run mulai" "$(g runcycles)  (sejak $(g runstart))"
[ "${N:-0}" -eq 0 ] && note "contributions kosong" "0 sejak run mulai" || bad "contributions kosong" "$N sejak run mulai"
note "siklus terakhir" "$(g lastcycle)"
note "posisi terbuka / baru 24j" "$(g openpos) / $(g newpos)"
[ "$(g blindfut)" -eq 0 ] && note "siklus tanpa data futures" "0" || bad "siklus tanpa data futures" "$(g blindfut)"
[ "$(g spoterr)" -eq 0 ] && note "error di log" "0" || bad "error di log" "$(g spoterr)"
B=$(g backup); [ "$B" = "db-$(date -u +%Y%m%d).db" ] && note "backup hari ini" "$B" || bad "backup hari ini" "${B:-tidak ada}"

echo "--- allocbot (demo) ---"
[ "$(g allocerr)" -eq 0 ] && note "error 24 jam" "0" || bad "error 24 jam" "$(g allocerr)"
A=$(g allocact)
[ "${A:-0}" -gt 0 ] && note "keputusan harian terjadi" "$A" || bad "keputusan harian terjadi" "0 — harusnya ada setelah 00:00 UTC"
note "debu OKB dilewati rapi" "$(g allocdust)"

echo
[ "$PROBLEMS" -eq 0 ] && echo "  >>> semua bersih <<<" || echo "  >>> $PROBLEMS hal perlu diperiksa <<<"
exit $(( PROBLEMS > 0 ))
