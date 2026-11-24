"""Telegram application setup and feature discovery.

A feature is a sub-package of `switchboard.features`. Each feature module
can define these names in its `__init__.py`:

    FEATURE_NAME   str. Shown in logs.
    COMMAND_HELP   list of (command, description). Shown by /help.
    register(application)   Add handlers and jobs to the Telegram app.

Core finds features by scanning the package. To add a feature, add a
folder. No change in core is needed.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from types import ModuleType

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import switchboard.features as features_pkg
from switchboard.core.services import Services
from switchboard.core.sessions import get_or_create_session

log = logging.getLogger(__name__)

SERVICES_KEY = "services"

CORE_HELP = [
    ("start", "Create your session and see a short welcome"),
    ("help", "List all commands"),
]


def get_services(context: ContextTypes.DEFAULT_TYPE) -> Services:
    """Return the shared Services object from a handler context."""
    return context.application.bot_data[SERVICES_KEY]


def discover_features() -> list[ModuleType]:
    """Import every sub-package of switchboard.features."""
    modules: list[ModuleType] = []
    for info in pkgutil.iter_modules(features_pkg.__path__):
        if info.ispkg:
            modules.append(importlib.import_module(f"{features_pkg.__name__}.{info.name}"))
    return modules


def build_help_text(features: list[ModuleType]) -> str:
    """Build the /help message from core and feature command lists."""
    lines = ["Commands:", ""]
    lines += [f"/{name} - {text}" for name, text in CORE_HELP]
    for module in features:
        entries = getattr(module, "COMMAND_HELP", [])
        if entries:
            lines += ["", f"{getattr(module, 'FEATURE_NAME', module.__name__)}:"]
            lines += [f"/{name} - {text}" for name, text in entries]
    return "\n".join(lines)


def build_application(services: Services) -> Application:
    """Create the Telegram application and register core and feature handlers."""
    application = Application.builder().token(services.settings.telegram_bot_token).build()
    application.bot_data[SERVICES_KEY] = services

    features = discover_features()
    help_text = build_help_text(features)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        db = get_services(context).db
        # Create the session row. Blocking DB work runs in a thread.
        import asyncio

        await asyncio.to_thread(get_or_create_session, db, user.id)
        await update.effective_message.reply_text(
            "Welcome to Switchboard.\n"
            "Each user has a private session. Your data is never shared.\n\n"
            "Run /connect to link your Gmail account. Run /help for all commands."
        )

    async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(help_text)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    for module in features:
        register = getattr(module, "register", None)
        if register is None:
            log.warning("Feature %s has no register() function. Skipped.", module.__name__)
            continue
        register(application)
        log.info("Loaded feature: %s", getattr(module, "FEATURE_NAME", module.__name__))

    return application
