"""Entry point. Run with: python -m switchboard

One process runs two things on one asyncio event loop:

    * the Telegram bot, using long polling,
    * the FastAPI app, served by uvicorn on 127.0.0.1 only.

The Cloudflare tunnel forwards the public OAuth callback URL to the FastAPI app.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn

from switchboard.core.config import ConfigError, load_settings
from switchboard.core.crypto import CryptoError
from switchboard.core.services import build_services
from switchboard.core.telegram_app import build_application
from switchboard.web.app import create_app

log = logging.getLogger("switchboard")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs at INFO level. Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def run() -> None:
    """Start the bot and the web server. Return after the web server stops."""
    settings = load_settings()
    services = build_services(settings)
    application = build_application(services)

    async def notify(telegram_id: int, text: str) -> None:
        await application.bot.send_message(chat_id=telegram_id, text=text)

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(services, notify),
            host=settings.host,
            port=settings.port,
            log_level="info",
        )
    )

    # `async with` initializes the bot and shuts it down on exit.
    async with application:
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        log.info("Bot is polling. Web server on http://%s:%s", settings.host, settings.port)
        try:
            # uvicorn handles SIGINT and SIGTERM and returns when asked to stop.
            await server.serve()
        finally:
            await application.updater.stop()
            await application.stop()


def main() -> None:
    _setup_logging()
    try:
        asyncio.run(run())
    except (ConfigError, CryptoError) as exc:
        log.error("%s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
