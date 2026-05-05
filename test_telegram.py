#!/usr/bin/env python3
"""Quick test: kirim pesan ke Telegram menggunakan kredensial dari .env"""

from config import HTTP_SESSION, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("ERROR: TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID belum diset di .env")
    raise SystemExit(1)

resp = HTTP_SESSION.post(
    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
    json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "✅ SpotSignal test — Telegram berhasil terhubung!",
        "parse_mode": "Markdown",
    },
    timeout=10,
)

if resp.status_code == 200:
    msg_id = resp.json()["result"]["message_id"]
    print(f"OK — pesan terkirim (message_id={msg_id})")
else:
    print(f"GAGAL — HTTP {resp.status_code}: {resp.text}")
    raise SystemExit(1)
