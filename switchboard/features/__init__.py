"""Feature packages. Each sub-package is one feature.

Core scans this package at start-up. A feature folder needs a `register(application)`
function in its `__init__.py`. See `switchboard/core/telegram_app.py` for the contract.
"""
