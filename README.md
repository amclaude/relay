# Relay

Relay is a multi-user Telegram bot. Any Telegram user can start a private session and connect their own Gmail account. Gmail is the first feature. More features can be added as new folders.

The bot can read and archive mail. It cannot send mail.

This is a portfolio demo. Google apps in Testing mode issue refresh tokens that expire after 7 days. After that, users run `/connect` again.

**Live demo:** https://t.me/RelayDemoBot

> **Disclaimer:** This is a demo that runs in Google OAuth Testing mode. It only accepts Google accounts that are registered as test users. Anyone else is blocked at Google's consent screen and cannot connect Gmail. Anyone can open the bot and run `/start`. To connect Gmail, the owner must first add your email address as a test user. If you want to try it, message claude@crawlshift.dev with the Gmail address you want to use.

## Usage

1. Open the bot at https://t.me/RelayDemoBot.
2. Send `/start`. This creates your session and shows a welcome.
3. Send `/connect`. The bot replies with a Google link. Tap the link and approve access. The link works for 10 minutes and for one use. The bot messages you when Gmail is connected.
4. Use the commands below.

The scope is `gmail.modify`. The bot can read and archive mail. It cannot send mail.

`<n>` in `/read` and `/archive` is the number of a message in the most recent `/unread` or `/search` listing. Each new listing replaces the old one.

If you run a Gmail command before you connect, the bot replies: `No Gmail account is connected. Run /connect first.`

### `/help`

Lists all commands.

### `/unread`

Shows the last 10 unread messages as a numbered list. Each entry has the subject, the sender and a short snippet. If there is none, the bot replies `No unread mail.`

```
/unread
```

### `/search <query>`

Searches Gmail. The query uses Gmail search syntax. The bot shows up to 10 matches as a numbered list. If nothing matches, the bot replies `No messages found.`

```
/search from:amazon newer_than:7d
```

### `/read <n>`

Shows message number `n` from the last list. The reply has the subject, sender, date and the plain text body. Long messages are cut and end with `[truncated]`.

```
/read 2
```

### `/archive <n>`

Removes message number `n` from the inbox. The bot replies `Archived.`

```
/archive 2
```

### `/alerts on|off`

Turns new-mail alerts on or off. The bot checks for new mail every 5 minutes while alerts are on.

```
/alerts on
```

### `/disconnect`

Revokes the Google token and deletes all stored data for your account. If Google does not confirm the revoke, the bot tells you to remove access at myaccount.google.com/permissions.

### Expired access

If the Google token has expired or was revoked, the bot tells you to run `/connect` again.

## Architecture

```
  Telegram user                          Browser
       |                                    |
       | commands (long polling)            | Google consent screen
       v                                    v
+---------------------------------------------------------------+
|  python -m relay         (one process, one event loop)        |
|                                                               |
|  +--------------------+          +-------------------------+  |
|  | Telegram bot       |          | FastAPI (uvicorn)       |  |
|  | python-telegram-bot|          | 127.0.0.1:8742          |  |
|  |                    |          |  GET /health            |  |
|  |  relay/            |          |  GET /oauth/google/     |  |
|  |   features/gmail/  |          |      callback           |  |
|  +---------+----------+          +------------+------------+  |
|            |                                  |               |
|            +---------------+------------------+               |
|                            v                                  |
|          relay/core: config, crypto (Fernet),                 |
|          sessions (OAuth state), db (SQLite)                  |
+---------------------------------------------------------------+
                             ^
                             | http://localhost:8742
                   Cloudflare tunnel
                             ^
                             | https://relay.crawlshift.dev
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
relay/
  __main__.py        entry point: bot + web server
  core/              config, crypto, db, sessions, services, telegram_app
  web/               FastAPI app: OAuth callback and health route
  features/
    gmail/           client, oauth, commands, alerts, register()
scripts/
  gen_fernet_key.py  prints a new FERNET_KEY
tests/               pytest suite, all external calls mocked
```

A feature is a folder in `relay/features/`. Its `__init__.py` defines `FEATURE_NAME`, `COMMAND_HELP` and `register(application)`. Core finds it at start-up. No change in core is needed.

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
   https://relay.crawlshift.dev/oauth/google/callback
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
| `PUBLIC_BASE_URL` | Public HTTPS base URL | `https://relay.crawlshift.dev` |
| `PORT` | Local port for FastAPI | `8742` |

Keep `.env` and the key secret. If you lose the key, stored tokens cannot be decrypted. Users must connect again.

### 4. Cloudflare tunnel

Map `https://relay.crawlshift.dev` to `http://localhost:8742`. FastAPI binds to `127.0.0.1` only. The tunnel is the only way in from the internet.

## Running

```
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m relay
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
