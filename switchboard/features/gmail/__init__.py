"""Gmail feature: connect an account, read mail, archive, and get alerts.

This package follows the feature contract described in
`switchboard/core/telegram_app.py`. Core finds it by scanning
`switchboard.features` and calls `register(application)`.
"""

from __future__ import annotations

from telegram.ext import Application, CommandHandler

from switchboard.features.gmail import alerts, commands

FEATURE_NAME = "Gmail"

COMMAND_HELP = [
    ("connect", "Link your Gmail account"),
    ("disconnect", "Revoke access and delete your stored data"),
    ("unread", "Show your last 10 unread messages"),
    ("search", "Search mail, for example /search from:alice"),
    ("read", "Read a message from the last list, for example /read 2"),
    ("archive", "Archive a message from the last list, for example /archive 2"),
    ("alerts", "Turn new-mail alerts on or off: /alerts on or /alerts off"),
]

# Delay before the first poll, in seconds. Gives the bot time to start.
_FIRST_POLL_DELAY = 30


def register(application: Application) -> None:
    """Add the Gmail command handlers and the alert polling job."""
    application.add_handler(CommandHandler("connect", commands.connect))
    application.add_handler(CommandHandler("disconnect", commands.disconnect))
    application.add_handler(CommandHandler("unread", commands.unread))
    application.add_handler(CommandHandler("search", commands.search))
    application.add_handler(CommandHandler("read", commands.read))
    application.add_handler(CommandHandler("archive", commands.archive))
    application.add_handler(CommandHandler("alerts", commands.alerts))

    application.job_queue.run_repeating(
        alerts.poll_all,
        interval=alerts.POLL_INTERVAL_SECONDS,
        first=_FIRST_POLL_DELAY,
        name="gmail-alerts",
    )
