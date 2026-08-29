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
# Without this guard `curl` exiting 127 becomes `000` below, and the operator is
# told to destroy a perfectly good VPS because its region looked blocked.
command -v curl >/dev/null ||
  die "curl is not installed. Install it first (apt install curl / dnf install curl)
      and re-run. A missing curl is not a blocked region."
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
  # Oracle Linux 9 / RHEL 9 ship curl-minimal, and `dnf install curl` tries to
  # swap it out — that fails with a conflict unless --allowerasing is passed.
  # curl is already present (step 0 used it), so do not ask for it here.
  sudo dnf install -y -q git ca-certificates gcc gcc-c++ make
else
  die "Unsupported distro: need apt or dnf"
fi

# Oracle Linux and Ubuntu both default to a firewall that blocks nothing
# outbound, which is all this bot needs — it opens no listening port.

# ── 2. Swap — insurance for the once-an-hour memory spike ───────────────────
# Measured on this workload: the bot idles at ~125 MB and peaks at ~320 MB for
# about six seconds per cycle, while both pipelines hold their DataFrames. On a
# 1 GB instance that peak plus the OS leaves little headroom, and an OOM kill
# mid-cycle would end a multi-week run silently. Swap is not meant to be USED
# here — swappiness is lowered so the kernel reaches for it only under real
# pressure, keeping the hourly spike in RAM where it belongs.
say "Checking swap"
mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
echo "    RAM: ${mem_mb} MB"

if [ "$(swapon --show --noheadings 2>/dev/null | wc -l)" -gt 0 ]; then
  echo "  ✓ swap already configured, leaving it alone"
elif [ "$mem_mb" -ge 2048 ]; then
  echo "  ✓ ${mem_mb} MB RAM is ample — no swapfile needed"
else
  avail_mb=$(df -Pm / | awk 'NR==2 {print $4}')
  if [ "$avail_mb" -lt 4096 ]; then
    warn "Only ${avail_mb} MB free on / — skipping swapfile to avoid filling the disk"
  elif sudo fallocate -l 2G /swapfile 2>/dev/null ||
       sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none 2>/dev/null; then
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    if sudo swapon /swapfile 2>/dev/null; then
      # Survive reboot, without duplicating the entry on a re-run.
      grep -q '^/swapfile ' /etc/fstab ||
        echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
      sudo sysctl -w vm.swappiness=10 >/dev/null
      grep -q '^vm.swappiness' /etc/sysctl.conf 2>/dev/null ||
        echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf >/dev/null
      echo "  ✓ 2 GB swapfile active, swappiness 10"
    else
      # Containerised VPS (OpenVZ/LXC) cannot take a swapfile. Not fatal.
      warn "swapon refused — this host probably cannot swap. Continuing without."
      sudo rm -f /swapfile
    fi
  else
    warn "Could not allocate /swapfile — continuing without swap"
  fi
fi

# ── 3. Python 3.14 via uv ───────────────────────────────────────────────────
# Ubuntu 24.04 ships 3.12 and Oracle Linux 9 ships 3.9; requirements.txt pins
# pandas 3.0.2, so let uv fetch the interpreter rather than fighting the distro.
#
# The system python is NEVER touched. On Oracle Linux dnf itself is written in
# python3.9 — replacing or upgrading it in place breaks the package manager and
# the usual way out is rebuilding the instance. uv installs a private
# interpreter under ~/.local and the venv points at that.
say "Installing uv + Python 3.14"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.14

# ── 4. Code ─────────────────────────────────────────────────────────────────
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

# ── 5. Secrets ──────────────────────────────────────────────────────────────
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

# ── 6. Record what this run is testing, BEFORE anything writes to the DB ──────────────────────────────────────
say "Recording the run manifest"
./venv/bin/python scripts/start_paper_run.py || true

# ── 7. One verification cycle before committing to the loop ─────────────────
say "Running one full cycle as a smoke test"
if ! ./venv/bin/python run_bot.py; then
  die "The verification cycle failed. Fix that before installing the service —
      a broken loop under Restart=always just fails every 30 seconds forever."
fi

# ── 8. systemd ──────────────────────────────────────────────────────────────
say "Installing the systemd service"
sed -e "s|__USER__|$SERVICE_USER|g" -e "s|__APP_DIR__|$APP_DIR|g" deploy/spotsignal.service \
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
