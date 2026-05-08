"""Telegram alert delivery — backward-compatibility shim.

Re-exports everything from the ``notifier`` package so existing import
paths (``from notifier import ...``) continue to work.
"""

from notifier.common import (  # noqa: F401
    _dir,
    _esc,
    _macro_banner,
    _max_score,
    _mode_label,
    _send_telegram_message,
    send_signal_alert,
)
from notifier.telegram import (  # noqa: F401
    _format_close_notification,
    _format_compact_signal_telegram,
    _format_consolidated_telegram,
    _send_combined_telegram,
)
