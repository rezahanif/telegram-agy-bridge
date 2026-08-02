# Antigravity CLI Telegram Bridge

A Telegram bot that bridges chat messages to the `agy` (Antigravity) CLI, running locally on your machine. Every `/` command in Telegram maps to a flag or subcommand on `agy`, driven by a single declarative command registry instead of one handler per command.

## How it works

```
Telegram message ──> bot.py ──> subprocess: agy [flags] -p "prompt" ──> reply sent back to Telegram
```

- **Plain text messages** become `agy -p "<text>"` calls, with any active session flags (model, effort, mode, etc.) attached automatically.
- **`/commands`** are declared once in `command_registry.py` and handled generically — adding a new command means adding one entry to the registry, not writing a new handler function.
- **Files/photos** sent to the bot are downloaded, then either inlined into the prompt (for text-like files) or referenced by path (for everything else).

## Project structure

| File | Purpose |
|---|---|
| `bot.py` | Telegram wiring: command dispatch, callback routing, `agy` invocation, message handling |
| `command_registry.py` | Declarative list of every `/command` — name, action type, how it maps to `agy` |
| `.env.example` | Template for required environment variables |

## Requirements

- Python 3.10+
- `agy` (Antigravity CLI) installed and authenticated locally
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

```bash
pip install -r requirements.txt
```

## Setup

1. Copy the env template and fill it in:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env`:
   ```bash
   TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
   TELEGRAM_ALLOWED_USER_ID=111111111,222222222   # your Telegram numeric user ID(s), comma-separated
   AGY_PATH=/root/.local/bin/agy                  # path to the agy binary
   WORKSPACE_DIR=/path/to/your/project             # working directory agy runs in
   ```

   To find your Telegram numeric user ID, message [@userinfobot](https://t.me/userinfobot).

3. Load the env and run:
   ```bash
   export $(cat .env | xargs)
   python3 bot.py
   ```

   Or run under systemd/Docker for persistence — see `telegram-agy-bridge.service` / `docker-compose.yml` if present in your setup.

## Usage

Once running, message your bot on Telegram:

- Send any text → runs as a prompt against `agy`, continuing the current session automatically.
- Type `/` → see the full list of available commands (auto-generated from `command_registry.py`).
- Commands that need a choice (e.g. `/model`, `/effort`) show inline buttons.
- Commands that need free text (e.g. `/project`) will ask you to reply with a value.
- Commands that change state permanently (e.g. `/newproject`, `/update`) ask for confirmation first.
- Send a file or photo with a caption → the file is downloaded and reviewed as part of the prompt.
- `/status` — see current model, effort, workspace, and whether a session is active.
- `/new` or `/reset` — start a fresh conversation (clears `--continue` state).

## Adding a new command

All 40+ `agy` commands are meant to be added the same way — no new Telegram-handling code required:

1. Add an entry to `COMMAND_REGISTRY` in `command_registry.py`:
   ```python
   CommandDef(
       name="mycommand",
       description="What this does",
       action_type="picker",       # picker | text_input | confirm | immediate
       invocation="session_flag",  # session_flag | standalone
       agy_flag="--my-flag",       # or agy_subcommand=["sub", "command"] for standalone
       choices=["a", "b", "c"],    # for picker
   )
   ```
2. Restart the bot — it's automatically wired into the `/` menu, callback routing, and dispatch.

See `command_registry.py`'s docstring for the full field reference and the four `action_type`s.

## Security notes

- **Never commit `.env` or hardcode your bot token/user ID in `bot.py`.** If a token is ever exposed publicly, regenerate it immediately via @BotFather.
- `TELEGRAM_ALLOWED_USER_ID` is a strict allowlist — every handler checks it before doing anything. Don't run this bot without setting it.
- File uploads are saved under `WORKSPACE_DIR`; the `/files` path traversal is blocked, but review this if you expose it beyond a single trusted user.

## Known limitations

- Single-process, in-memory session state — restarting the bot clears active sessions (by design; conversation history lives in `agy` itself via `--continue`/`--conversation`).
- No built-in rate limiting; intended for personal/small-team use, not public deployment.
- `choices_from_agy` pickers (e.g. `/model` populated via `agy models`) depend on that subcommand's exact output format — verify parsing matches your installed `agy` version if commands seem to show odd choices.
