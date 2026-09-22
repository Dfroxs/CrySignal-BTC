#!/bin/zsh
# The five invocations EXACTLY as pre-registered in
# docs/superpowers/specs/2026-09-21-exit-prereg.md §2. Do not edit.
set -u
cd /Users/dfroxs/Playground/Python/SpotSignal
OUT=.superpowers/sdd/2026-09-21-exit-mechanics/run
SYMS=BTC/USDT,ETH/USDT,BNB/USDT,XRP/USDT,LINK/USDT
YEARS=2020,2021,2022,2023
run() {  # run <mode> <hypothesis>
  echo "=== $2 $1 : started $(date -u +%FT%TZ) ==="
  ./venv/bin/python scripts/exit_ic.py --mode "$1" \
      --symbols "$SYMS" --years "$YEARS" --stride 6 --only "$2" \
      > "$OUT/$2-$1.log" 2>&1
  echo "=== $2 $1 : exit $? at $(date -u +%FT%TZ), $(grep -c . "$OUT/$2-$1.log") lines ==="
}
run spot     H1
run spot     H2
run futures  H2
run spot     H3
run futures  H3
echo "=== ALL FIVE COMPLETE $(date -u +%FT%TZ) ==="
