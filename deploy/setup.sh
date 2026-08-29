#!/usr/bin/env bash
# Provision a fresh VPS to run the SpotSignal paper bot under systemd.
#
#   curl -fsSL https://raw.githubusercontent.com/Dfroxs/CrySignal-BTC/develop/deploy/setup.sh | bash
# or, from a clone:
#   bash deploy/setup.sh
#
# Idempotent: safe to re-run after a failure.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Dfroxs/CrySignal-BTC.git}"
BRANCH="${BRANCH:-develop}"
APP_DIR="${APP_DIR:-$HOME/SpotSignal}"
SERVICE_USER="$(id -un)"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\033[31m  ✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 0. Binance reachability — the one check that decides everything ─────────
# api.binance.com answers 403 from blocked jurisdictions (all US regions among
# them). The code falls back to a public mirror for SPOT reads, but that mirror
# serves no futures endpoints, so funding, L/S, open interest, basis and taker
# ratio would all degrade to NEUTRAL — 7.5 of the 26.5-point futures ceiling,
# silently. A paper run on such a host measures a different system.
say "Checking Binance reachability from this host"
spot_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
  'https://api.binance.com/api/v3/ping' || echo 000)
fut_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
  'https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT' || echo 000)
echo "    api.binance.com  (spot)    -> $spot_code"
echo "    fapi.binance.com (futures) -> $fut_code"
if [ "$spot_code" != "200" ] || [ "$fut_code" != "200" ]; then
  die "Binance is not reachable from this region (need 200/200).
      Do NOT run the validation here — the futures pipeline would lose 28% of
      its condition set and the run would be invalid. Rebuild the instance in a
      region where both return 200 (Singapore, Tokyo and EU regions normally do;
      US regions do not)."
fi
echo "  ✓ both endpoints reachable"

# ── 1. System packages ──────────────────────────────────────────────────────
say "Installing system packages"
if command -v apt-get >/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq git curl ca-certificates build-essential
elif command -v dnf >/dev/null; then
  sudo dnf install -y -q git curl ca-certificates gcc gcc-c++ make
else
  die "Unsupported distro: need apt or dnf"
fi

# Oracle Linux and Ubuntu both default to a firewall that blocks nothing
# outbound, which is all this bot needs — it opens no listening port.

# ── 2. Python 3.14 via uv ───────────────────────────────────────────────────
# Ubuntu 24.04 ships 3.12 and Oracle Linux 9 ships 3.9; requirements.txt pins
# pandas 3.0.2, so let uv fetch the interpreter rather than fighting the distro.
say "Installing uv + Python 3.14"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.14

# ── 3. Code ─────────────────────────────────────────────────────────────────
say "Fetching the repository"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
  git -C "$APP_DIR" checkout --quiet "$BRANCH"
  git -C "$APP_DIR" pull --quiet --ff-only origin "$BRANCH"
else
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

say "Building the virtualenv"
uv venv --python 3.14 venv
# ARM64 wheels exist for numpy/pandas/ccxt; a source build here would mean the
# toolchain is missing a wheel — surface it rather than silently compiling.
VIRTUAL_ENV="$APP_DIR/venv" uv pip install --quiet -r requirements.txt
./venv/bin/python -c "import ccxt, pandas, numpy; print('  ✓ imports OK')"

# ── 4. Secrets ──────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  warn "Created .env from the example — it has NO Telegram credentials yet."
  warn "Edit $APP_DIR/.env and fill TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID,"
  warn "then re-run this script. Leave the threshold lines commented out:"
  warn "setting them switches OFF the adaptive controller."
  exit 0
fi
if ! grep -qE '^TELEGRAM_BOT_TOKEN=.+' .env; then
  warn "TELEGRAM_BOT_TOKEN is empty in .env — the bot will run but stay silent."
fi

# ── 5. One verification cycle before committing to the loop ─────────────────
say "Running one full cycle as a smoke test"
if ! ./venv/bin/python run_bot.py; then
  die "The verification cycle failed. Fix that before installing the service —
      a broken loop under Restart=always just fails every 30 seconds forever."
fi

# ── 6. systemd ──────────────────────────────────────────────────────────────
say "Installing the systemd service"
sed "s|__USER__|$SERVICE_USER|g" deploy/spotsignal.service \
  | sudo tee /etc/systemd/system/spotsignal.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now spotsignal.service
sleep 3
sudo systemctl --no-pager --lines=0 status spotsignal.service || true

cat <<EOF

$(say "Done")
  Status   : sudo systemctl status spotsignal
  Follow   : tail -f $APP_DIR/paper_run.log
  Report   : cd $APP_DIR && ./venv/bin/python analyze.py
  Stop     : sudo systemctl stop spotsignal

  The run is now frozen against data/paper_run_manifest.json. Changing any
  parameter while it is live voids the sample.
EOF
