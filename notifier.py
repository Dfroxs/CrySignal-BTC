"""Telegram and Discord alert delivery — backward-compatibility shim.

Re-exports everything from the ``notifier`` package so existing import
paths (``from notifier import ...``) continue to work.

New code should import from:

    from notifier.telegram import _format_consolidated_telegram, _format_close_notification
    from notifier.discord import _format_combined_discord, send_discord_alert
    from notifier.common import send_signal_alert, _send_telegram_message
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
from notifier.discord import (  # noqa: F401
    _format_combined_discord,
    _format_discord,
    _format_position_status_discord,
    _format_section_discord,
    _outcome_line,
    _send_combined_discord,
    send_discord_alert,
)
from notifier.telegram import (  # noqa: F401
    _format_close_notification,
    _format_compact_signal_telegram,
    _format_consolidated_telegram,
    _send_combined_telegram,
)
