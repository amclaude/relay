# Switchboard

Switchboard is a multi-user Telegram bot. Any Telegram user can start a private session and connect their own Gmail account. Gmail is the first feature. More features can be added as new folders.

The bot can read and archive mail. It cannot send mail.

This is a portfolio demo. Google apps in Testing mode issue refresh tokens that expire after 7 days. After that, users run `/connect` again.

## Architecture

```
  Telegram user                          Browser
       |                                    |
       | commands (long polling)            | Google consent screen
       v                                    v
+---------------------------------------------------------------+
|  python -m switchboard   (one process, one event loop)        |
|                                                               |
|  +--------------------+          +-------------------------+  |
|  | Telegram bot       |          | FastAPI (uvicorn)       |  |
|  | python-telegram-bot|          | 127.0.0.1:8742          |  |
|  |                    |          |  GET /health            |  |
|  |  switchboard/      |          |  GET /oauth/google/     |  |
|  |   features/gmail/  |          |      callback           |  |
|  +---------+----------+          +------------+------------+  |
|            |                                  |               |
|            +---------------+------------------+               |
|                            v                                  |
|          switchboard/core: config, crypto (Fernet),          |
|          sessions (OAuth state), db (SQLite)                  |
+---------------------------------------------------------------+
                             ^
                             | http://localhost:8742
                   Cloudflare tunnel
                             ^
                             | https://switchboard.crawlshift.dev
                         Google redirect
```

### Connect flow

1. The user sends `/connect`. The bot creates a random, single-use `state` tied to the Telegram ID. It expires after 10 minutes.
2. The bot replies with a Google consent link that contains the `state`.
3. The user approves access. Google redirects to `/oauth/google/callback`.
4. The callback checks and consumes the `state`. It trades the code for a refresh token.
5. The refresh token is encrypted with Fernet and stored. The bot messages the user that Gmail is connected.

### Layout

```
switchboard/
  __main__.py        entry point: bot + web server
  core/              config, crypto, db, sessions, services, telegram_app
  web/               FastAPI app: OAuth callback and health route
  features/
    gmail/           client, oauth, commands, alerts, register()
scripts/
  gen_fernet_key.py  prints a new FERNET_KEY
tests/               pytest suite, all external calls mocked
```

A feature is a folder in `switchboard/features/`. Its `__init__.py` defines `FEATURE_NAME`, `COMMAND_HELP` and `register(application)`. Core finds it at start-up. No change in core is needed.

## Commands

| Command | What it does |
| --- | --- |
| `/start` | Create your session and show a welcome |
| `/help` | List all commands |
| `/connect` | Get a link to connect your Gmail account |
| `/disconnect` | Revoke the Google token and delete all stored data |
| `/unread` | Show the last 10 unread messages |
| `/search <query>` | Search Gmail. Example: `/search from:alice has:attachment` |
| `/read <n>` | Read message number `n` from the last list |
| `/archive <n>` | Archive message number `n` from the last list |
| `/alerts on\|off` | Turn new-mail alerts on or off (checked every 5 minutes) |

If the Google token has expired or was revoked, the bot tells the user to run `/connect` again.

## Setup

Requires Python 3.11 or newer.

### 1. Create the Telegram bot

1. Open [@BotFather](https://t.me/BotFather) in Telegram.
2. Send `/newbot` and follow the prompts.
3. Copy the bot token. It is the value for `TELEGRAM_BOT_TOKEN`.

### 2. Create the Google OAuth client

1. Open the [Google Cloud console](https://console.cloud.google.com/) and create a project.
2. Enable the **Gmail API** (APIs & Services, Library).
3. Configure the **OAuth consent screen**. Choose user type External. Leave the publishing status as **Testing**.
4. Add these scopes: `gmail.modify`, `userinfo.email` and `openid`.
5. Under **Test users**, add the Google account of every person who will use the bot. Only test users can connect while the app is in Testing mode.
6. Under Credentials, create an **OAuth client ID** of type **Web application**.
7. Add this exact **Authorized redirect URI**:

   ```
   https://switchboard.crawlshift.dev/oauth/google/callback
   ```

8. Copy the client ID and client secret.

### 3. Configure the environment

```
cp .env.example .env
python scripts/gen_fernet_key.py
```

Paste the printed key into `FERNET_KEY` in `.env`. Fill in the other values.

| Variable | Meaning | Default |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Token from BotFather | required |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | required |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | required |
| `FERNET_KEY` | Key that encrypts refresh tokens | required |
| `PUBLIC_BASE_URL` | Public HTTPS base URL | `https://switchboard.crawlshift.dev` |
| `PORT` | Local port for FastAPI | `8742` |

Keep `.env` and the key secret. If you lose the key, stored tokens cannot be decrypted. Users must connect again.

### 4. Cloudflare tunnel

Map `https://switchboard.crawlshift.dev` to `http://localhost:8742`. FastAPI binds to `127.0.0.1` only. The tunnel is the only way in from the internet.

### 5. Install and run

```
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m switchboard
```

The bot starts long polling and the web server starts on `127.0.0.1:8742`.

## Tests

```
.venv/bin/python -m pytest
```

The tests cover the crypto helper, OAuth state handling, the database layer and the callback route. Google and Telegram are never called.

## Security notes

- Refresh tokens are encrypted at rest with Fernet. The plain token is never stored.
- The OAuth `state` is random, single-use, tied to one Telegram user and expires after 10 minutes.
- `/disconnect` revokes the token at Google and deletes the stored account, pending states and user row.
- The scope is `gmail.modify`. It allows reading and archiving. It does not allow sending.
