"""
Phase 1 skeleton — Telegram bot with generic dispatch.

Wires the CommandDef registry into python-telegram-bot. All per-command logic
flows through one `generic_command_entry` (for /commands) and one
`button_callback` (for inline keyboard taps). Adding a 41st command to the
registry needs zero new handler code.

Phase 1 review fixes applied (see PHASE_1_REVIEW.md):
  - asyncio.create_subprocess_exec instead of subprocess.run (B1) — fixes
    event-loop blocking, enables /stop
  - callback_data uses an 8-char picker_id + index, not the raw value (B2) —
    keeps us under Telegram's 64-byte cap even for long paths
  - Picker parser stubbed with a clear TODO + best-effort line-based fallback (B3)
  - Pickers mark the current value with ✓ (B4)
  - Pagination for pickers with > PAGE_SIZE choices (B5)
  - Stale-tap protection via atomic dict.pop() + monotonic TTL (B6)
  - session_flag commands do NOT reset has_active_session — only resets_session=True does (B7)
  - Free-text input has a Cancel button + TTL (B8)
  - execute_command takes a reply callable, not an Update shim (B9)
  - button_callback no longer double-replies (B10)
  - /stop implemented (B11)
  - subprocess uses errors="replace" (B12)
  - Markdown V2 with plain-text fallback (B13)
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from typing import Awaitable, Callable, Optional

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from command_registry import COMMAND_REGISTRY, CommandDef, resolve_command, telegram_menu_commands

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS_ENV = os.getenv("TELEGRAM_ALLOWED_USER_ID")
if not TELEGRAM_BOT_TOKEN or not ALLOWED_USER_IDS_ENV:
    raise ValueError("CRITICAL: TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_ID are required.")
ALLOWED_USER_IDS = {int(x.strip()) for x in ALLOWED_USER_IDS_ENV.split(",") if x.strip()}

AGY_PATH = os.getenv("AGY_PATH", "/root/.local/bin/agy")
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/project")
AGY_TIMEOUT = int(os.getenv("AGY_TIMEOUT", "180"))
AGY_LONG_TIMEOUT = int(os.getenv("AGY_LONG_TIMEOUT", "600"))
AGY_SUBCMD_TIMEOUT = int(os.getenv("AGY_SUBCMD_TIMEOUT", "60"))

PICKER_TTL_SECONDS = int(os.getenv("PICKER_TTL_SECONDS", "300"))
FREE_TEXT_TTL_SECONDS = int(os.getenv("FREE_TEXT_TTL_SECONDS", "180"))
PICKER_PAGE_SIZE = int(os.getenv("PICKER_PAGE_SIZE", "8"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
log = logging.getLogger("agy-bridge")

# ---------------------------------------------------------------------------
# Per-chat state
# ---------------------------------------------------------------------------
#   flags: dict[agy_flag, str | list[str]]
#       - non-repeatable: {"--model": "gemini-3.1-pro-high"}
#       - repeatable:     {"--add-dir": ["/project/src", "/project/tests"]}
#       - toggle (bare):  {"--sandbox": True}  (presence = on)
#   has_active_session: bool
#   pending_text_input: dict | None   -> next plain-text message is captured
user_sessions: dict[int, dict] = {}

# Picker state: picker_id -> {cmd_name, chat_id, choices, labels, current_value,
#                              page, msg_id, expires_at}
pickers: dict[str, dict] = {}

# Track running agy subprocesses per chat so /stop can kill them
running_procs: dict[int, asyncio.subprocess.Process] = {}


def get_session(chat_id: int) -> dict:
    if chat_id not in user_sessions:
        user_sessions[chat_id] = {
            "flags": {},
            "has_active_session": False,
            "pending_text_input": None,
        }
    return user_sessions[chat_id]


def is_allowed(user_id: int) -> bool:
    return user_id in ALLOWED_USER_IDS


# ---------------------------------------------------------------------------
# agy invocation builders  (Phase 2 will get unit tests for this)
# ---------------------------------------------------------------------------


def build_session_agy_cmd(session: dict, prompt: str) -> list[str]:
    """For normal chat messages: base agy call + all active session_flag state."""
    cmd: list[str] = [AGY_PATH]
    if session["has_active_session"]:
        cmd.append("--continue")
    for flag, value in session["flags"].items():
        if value is True:
            # bare toggle flag (e.g. --sandbox, --debug)
            cmd.append(flag)
        elif isinstance(value, list):
            # repeatable flag (e.g. --add-dir)
            if not value:
                continue
            cmd.append(flag)
            cmd.extend(value)
        elif value:  # non-empty string
            cmd.extend([flag, value])
    cmd.extend(["-p", prompt])
    return cmd


def build_standalone_agy_cmd(cmd_def: CommandDef, value: Optional[str] = None) -> list[str]:
    """For standalone subcommands: independent of session state."""
    cmd = [AGY_PATH, *cmd_def.agy_subcommand]
    if value is not None:
        cmd.append(value)
    return cmd


# ---------------------------------------------------------------------------
# Async subprocess runner  (B1 fix)
# ---------------------------------------------------------------------------


async def run_agy_async(chat_id: int, cmd: list[str], timeout: int = AGY_TIMEOUT) -> str:
    """Run agy as an async subprocess. Tracks the proc so /stop can kill it."""
    log.info("Executing: %s (cwd=%s)", cmd, WORKSPACE_DIR)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=WORKSPACE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return f"❌ agy binary not found at {AGY_PATH}"
    except Exception as e:
        return f"❌ Failed to start agy: {e}"

    running_procs[chat_id] = proc
    try:
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return f"⚠️ Command timed out after {timeout}s."
        stdout = (stdout_b or b"").decode(errors="replace")
        stderr = (stderr_b or b"").decode(errors="replace")
        return stdout.strip() or stderr.strip() or "Task finished with no output."
    finally:
        running_procs.pop(chat_id, None)


async def run_agy_capture(cmd: list[str], timeout: int = AGY_SUBCMD_TIMEOUT) -> str:
    """One-shot capture for standalone subcommands (no /stop tracking needed)."""
    log.info("Running (capture): %s", cmd)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=WORKSPACE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return f"❌ agy binary not found at {AGY_PATH}"
    except Exception as e:
        return f"❌ Failed to start agy: {e}"
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return f"⚠️ Command timed out after {timeout}s."
    stdout = (stdout_b or b"").decode(errors="replace")
    stderr = (stderr_b or b"").decode(errors="replace")
    return stdout.strip() or stderr.strip() or "(no output)"


# ---------------------------------------------------------------------------
# Safe send / edit (B13 fix — Markdown V2 with plain-text fallback)
# ---------------------------------------------------------------------------


async def safe_reply(message, text: str, *, reply_markup=None, parse_mode=None):
    if parse_mode:
        try:
            return await message.reply_text(
                text, parse_mode=parse_mode, reply_markup=reply_markup
            )
        except BadRequest:
            pass
    return await message.reply_text(text, reply_markup=reply_markup)


async def safe_edit(query, text: str, *, parse_mode=None, reply_markup=None):
    try:
        return await query.edit_message_text(
            text=text, parse_mode=parse_mode, reply_markup=reply_markup
        )
    except BadRequest:
        try:
            return await query.edit_message_text(text=text, reply_markup=reply_markup)
        except Exception:
            pass


async def send_long_output(message, output: str):
    """Split long output into 4000-char chunks with Markdown fallback."""
    if not output:
        await message.reply_text("(no output)")
        return
    chunks = [output[i : i + 4000] for i in range(0, len(output), 4000)]
    for chunk in chunks:
        try:
            await message.reply_text(f"```\n{chunk}\n```", parse_mode=ParseMode.MARKDOWN)
        except BadRequest:
            await message.reply_text(chunk)


# ---------------------------------------------------------------------------
# Picker state helpers (B2 + B5 + B6 fixes)
# ---------------------------------------------------------------------------


async def parse_agy_choices(cmd_def: CommandDef) -> list[tuple[str, str]]:
    """Run `agy <choices_from_agy>` and parse into [(label, value), ...].

    TODO (Phase 3): real parser once we see the actual `agy models` / `agy agents`
    output shape. For now, line-based fallback: each non-empty line is one
    option; first whitespace token is the value, full line is the label.
    """
    if not cmd_def.choices_from_agy:
        return []
    out = await run_agy_capture([AGY_PATH, *cmd_def.choices_from_agy])
    opts: list[tuple[str, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.endswith(":") and len(line) < 80:
            continue  # skip section headers
        tokens = line.split()
        value = tokens[0] if tokens else line
        label = line if len(line) <= 60 else line[:57] + "…"
        opts.append((label, value))
        if len(opts) >= 50:  # safety cap
            break
    return opts


def make_picker_state(
    chat_id: int,
    cmd_def: CommandDef,
    choices: list[tuple[str, str]],
    current_value: Optional[str],
) -> str:
    """Create a picker state entry, return its id."""
    picker_id = secrets.token_urlsafe(6)[:8]
    pickers[picker_id] = {
        "chat_id": chat_id,
        "cmd_name": cmd_def.name,
        "choices": choices,
        "current_value": current_value,
        "page": 0,
        "msg_id": None,
        "expires_at": time.monotonic() + PICKER_TTL_SECONDS,
    }
    return picker_id


async def render_picker(bot, picker_id: str) -> None:
    """Render (or re-render) the picker message, editing in place if possible."""
    state = pickers.get(picker_id)
    if not state:
        return
    choices = state["choices"]
    page = state["page"]
    total_pages = max(1, (len(choices) + PICKER_PAGE_SIZE - 1) // PICKER_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    state["page"] = page

    page_opts = choices[page * PICKER_PAGE_SIZE : page * PICKER_PAGE_SIZE + PICKER_PAGE_SIZE]
    rows: list[list[InlineKeyboardButton]] = []
    for i, (label, value) in enumerate(page_opts):
        abs_idx = page * PICKER_PAGE_SIZE + i
        marker = " ✓" if value == state["current_value"] else ""
        rows.append(
            [InlineKeyboardButton(label + marker, callback_data=f"cp:{picker_id}:{abs_idx}")]
        )

    # Pagination nav row
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"cp:{picker_id}:p:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data=f"cp:{picker_id}:noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data=f"cp:{picker_id}:p:{page + 1}"))
        rows.append(nav)

    # Cancel row
    rows.append([InlineKeyboardButton("✗ Cancel", callback_data=f"cp:{picker_id}:cancel")])

    cmd_def = resolve_command(state["cmd_name"])
    title = f"Select a value for /{state['cmd_name']}:" if cmd_def is None else cmd_def.description
    text = f"*{title}*"
    if state["current_value"]:
        text += f"\n\nCurrent: `{state['current_value']}`"

    markup = InlineKeyboardMarkup(rows)
    chat_id = state["chat_id"]
    if state["msg_id"]:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=state["msg_id"],
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=markup,
            )
            return
        except BadRequest:
            pass  # fall through to send new
    msg = await bot.send_message(
        chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup
    )
    state["msg_id"] = msg.message_id


# ---------------------------------------------------------------------------
# Generic per-action_type handlers
# ---------------------------------------------------------------------------


async def handle_picker(update: Update, cmd_def: CommandDef, session: dict):
    chat_id = update.effective_chat.id

    # Resolve choices
    if cmd_def.choices:
        labels = cmd_def.choice_labels or cmd_def.choices
        choices = list(zip(labels, cmd_def.choices))
    elif cmd_def.choices_from_agy:
        choices = await parse_agy_choices(cmd_def)
    else:
        choices = []

    if not choices:
        await safe_reply(update.message, f"No choices available for /{cmd_def.name}.")
        return

    current_value = session["flags"].get(cmd_def.agy_flag) if cmd_def.agy_flag else None
    if isinstance(current_value, list):
        current_value = current_value[0] if current_value else None

    picker_id = make_picker_state(chat_id, cmd_def, choices, current_value)
    await render_picker(update.get_bot(), picker_id)


async def handle_toggle(update: Update, cmd_def: CommandDef, session: dict):
    """Render a toggle button showing current on/off state."""
    chat_id = update.effective_chat.id
    is_on = bool(session["flags"].get(cmd_def.agy_flag, False))

    # If enabling and the command requires confirmation, show the warning first
    if not is_on and cmd_def.confirm_if_enabling:
        keyboard = [[
            InlineKeyboardButton("⚠️ Confirm", callback_data=f"tg:{cmd_def.name}:on"),
            InlineKeyboardButton("✗ Cancel", callback_data=f"tg:{cmd_def.name}:cancel"),
        ]]
        await safe_reply(
            update.message,
            f"⚠️ *{cmd_def.description}*\n\n`{cmd_def.agy_flag}` auto-approves ALL tool calls.\n"
            "agy can read/write any file, run any shell command, make network calls.\n"
            "Only enable in a throwaway sandbox.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    label = f"{'🟢 ON' if is_on else '⚫ OFF'} — click to toggle"
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"tg:{cmd_def.name}:flip")]]
    )
    await safe_reply(
        update.message,
        f"*{cmd_def.name}*\nFlag: `{cmd_def.agy_flag}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )


async def handle_confirm(update: Update, cmd_def: CommandDef, session: dict):
    keyboard = [[
        InlineKeyboardButton("✅ Confirm", callback_data=f"cf:{cmd_def.name}:yes"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"cf:{cmd_def.name}:no"),
    ]]
    await safe_reply(
        update.message,
        cmd_def.confirm_prompt or f"Run /{cmd_def.name}?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_text_input_prompt(update: Update, cmd_def: CommandDef, session: dict):
    """Set pending_text_input so the next plain-text message is captured as the arg."""
    session["pending_text_input"] = {
        "cmd_name": cmd_def.name,
        "expires_at": time.monotonic() + FREE_TEXT_TTL_SECONDS,
    }
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✗ Cancel", callback_data=f"ft:{cmd_def.name}:cancel")]]
    )
    await safe_reply(
        update.message,
        f"Reply with the value for /{cmd_def.name}:",
        reply_markup=keyboard,
    )


async def execute_command(
    reply: Callable[[str, Optional[InlineKeyboardMarkup]], Awaitable[None]],
    chat_id: int,
    cmd_def: CommandDef,
    session: dict,
    value: Optional[str] = None,
) -> str:
    """Actually run the command once we have whatever input it needed.

    `reply` is a callable that sends a message to the right chat — abstracts
    over Update vs CallbackQuery so we don't need the _Shim hack (B9 fix).
    Returns the human-readable result text so callers can decide what to do.
    """
    if cmd_def.invocation == "session_flag":
        # Mutate session state (Phase 2 will unit-test this)
        if cmd_def.agy_flag:
            if cmd_def.repeatable:
                bucket = session["flags"].setdefault(cmd_def.agy_flag, [])
                if value and value not in bucket:
                    bucket.append(value)
            elif cmd_def.action_type == "toggle":
                # toggle flip happened before reaching here; nothing to apply
                pass
            elif value is not None:
                session["flags"][cmd_def.agy_flag] = value
                # Handle conflicts (R6)
                for conflicting in cmd_def.conflicts_with:
                    session["flags"].pop(conflicting, None)

        if cmd_def.resets_session:
            session["has_active_session"] = False
            session["flags"].pop("--continue", None)

        result = f"✅ /{cmd_def.name} set"
        if value is not None:
            result += f" to `{value}`"
        await reply(result, None)
        return result

    # standalone
    if cmd_def.agy_subcommand == ():
        # In-process commands (e.g. /stop, /status, /help) — handle by name
        if cmd_def.name == "stop":
            return await _cmd_stop(reply, chat_id)
        # others fall through to "not implemented"
        await reply(f"⚠️ /{cmd_def.name} not implemented yet.", None)
        return "not implemented"

    status_text = "⏳ Running…"
    await reply(status_text, None)  # caller may replace this with status_msg handling
    timeout = AGY_LONG_TIMEOUT if cmd_def.long_running else AGY_SUBCMD_TIMEOUT
    output = await run_agy_capture(
        build_standalone_agy_cmd(cmd_def, value), timeout=timeout
    )
    await reply(output, None)
    return output


async def _cmd_stop(reply, chat_id: int) -> str:
    proc = running_procs.get(chat_id)
    if not proc:
        await reply("Nothing is currently running.", None)
        return "nothing running"
    try:
        proc.kill()
        await reply("🛑 Killed the running agy command.", None)
        return "killed"
    except ProcessLookupError:
        await reply("Process already exited.", None)
        return "already exited"


# ---------------------------------------------------------------------------
# Telegram wiring
# ---------------------------------------------------------------------------


async def generic_command_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("Unauthorized access.")
        return

    cmd_name = (update.message.text or "").split()[0][1:].split("@")[0].lower()
    cmd_def = resolve_command(cmd_name)
    if not cmd_def:
        await update.message.reply_text(f"Unknown command: /{cmd_name}. Try /help.")
        return

    if not cmd_def.implemented:
        await update.message.reply_text(
            f"⚠️ /{cmd_def.name} is registered but not implemented yet (Phase 3 batch pending)."
        )
        return

    session = get_session(update.effective_chat.id)

    if cmd_def.action_type == "picker":
        await handle_picker(update, cmd_def, session)
    elif cmd_def.action_type == "toggle":
        await handle_toggle(update, cmd_def, session)
    elif cmd_def.action_type == "confirm":
        await handle_confirm(update, cmd_def, session)
    elif cmd_def.action_type == "text_input":
        await handle_text_input_prompt(update, cmd_def, session)
    elif cmd_def.action_type == "immediate":
        async def reply(text, markup):
            if markup:
                await update.message.reply_text(text, reply_markup=markup)
            else:
                await send_long_output(update.message, text) if "\n" in text else await update.message.reply_text(text)

        await execute_command(reply, update.effective_chat.id, cmd_def, session)
    else:
        await update.message.reply_text(f"Unknown action_type: {cmd_def.action_type}")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_allowed(query.from_user.id):
        await query.answer("Unauthorized.", show_alert=True)
        return

    data = query.data or ""
    chat_id = query.message.chat.id
    session = get_session(chat_id)

    # ----- Prefix-routed dispatch (Hermes Rule 1) -----
    if data.startswith("cp:"):
        await _handle_picker_callback(query, data, session)
        return
    if data.startswith("tg:"):
        await _handle_toggle_callback(query, data, session)
        return
    if data.startswith("cf:"):
        await _handle_confirm_callback(query, data, session)
        return
    if data.startswith("ft:"):
        await _handle_freetext_callback(query, data, session)
        return

    await query.answer()  # unknown prefix — no-op toast


async def _handle_picker_callback(query, data: str, session: dict):
    parts = data.split(":")
    if len(parts) < 3:
        await query.answer()
        return
    picker_id = parts[1]
    action = parts[2]

    state = pickers.pop(picker_id, None)
    if not state or state["expires_at"] < time.monotonic():
        await query.answer("Picker expired — run the command again.")
        await safe_edit(query, "⌛ Picker expired.", reply_markup=None)
        return

    cmd_def = resolve_command(state["cmd_name"])
    if not cmd_def:
        await query.answer("Unknown command.")
        return

    if action == "noop":
        # page counter — non-interactive
        pickers[picker_id] = state
        await query.answer()
        return

    if action == "cancel":
        await query.answer("Cancelled")
        await safe_edit(query, f"~~{state['cmd_name']}~~ cancelled.", reply_markup=None)
        return

    if action == "p":
        if len(parts) < 4:
            await query.answer()
            pickers[picker_id] = state
            return
        try:
            new_page = int(parts[3])
        except ValueError:
            await query.answer("Bad page")
            pickers[picker_id] = state
            return
        state["page"] = new_page
        pickers[picker_id] = state
        await render_picker(query.bot, picker_id)
        await query.answer()
        return

    # numeric index
    try:
        idx = int(action)
    except ValueError:
        await query.answer("Bad callback")
        return
    choices = state["choices"]
    if idx < 0 or idx >= len(choices):
        await query.answer("Out of range")
        pickers[picker_id] = state
        return

    label, value = choices[idx]
    await query.answer(f"Selected: {label}")

    async def reply(text, markup):
        await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    await execute_command(reply, state["chat_id"], cmd_def, session, value=value)


async def _handle_toggle_callback(query, data: str, session: dict):
    parts = data.split(":")
    if len(parts) < 3:
        await query.answer()
        return
    cmd_name = parts[1]
    action = parts[2]
    cmd_def = resolve_command(cmd_name)
    if not cmd_def or cmd_def.action_type != "toggle":
        await query.answer("Unknown toggle")
        return

    if action == "cancel":
        await query.answer("Cancelled")
        await safe_edit(query, "Toggle cancelled.", reply_markup=None)
        return

    if action == "on":
        # Confirmed enabling of a destructive toggle (e.g. yolo)
        session["flags"][cmd_def.agy_flag] = True
        for conflicting in cmd_def.conflicts_with:
            session["flags"].pop(conflicting, None)
        await query.answer("⚠️ Enabled")
        await safe_edit(
            query,
            f"⚠️ *{cmd_def.name}* enabled.\nFlag: `{cmd_def.agy_flag}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=None,
        )
        return

    if action == "flip":
        is_on = bool(session["flags"].get(cmd_def.agy_flag, False))
        # If turning on a confirm_if_enabling toggle, show the gate first
        if not is_on and cmd_def.confirm_if_enabling:
            # Re-render the confirm keyboard
            keyboard = [[
                InlineKeyboardButton("⚠️ Confirm", callback_data=f"tg:{cmd_def.name}:on"),
                InlineKeyboardButton("✗ Cancel", callback_data=f"tg:{cmd_def.name}:cancel"),
            ]]
            await query.answer("Confirmation required")
            await safe_edit(
                query,
                f"⚠️ *{cmd_def.description}*\n\n`{cmd_def.agy_flag}` auto-approves ALL tool calls.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        # Plain flip
        session["flags"][cmd_def.agy_flag] = not is_on
        new_state = "🟢 ON" if session["flags"][cmd_def.agy_flag] else "⚫ OFF"
        if not session["flags"][cmd_def.agy_flag]:
            # turning off — remove the flag entirely so it doesn't appear in build_session_agy_cmd
            session["flags"].pop(cmd_def.agy_flag, None)
        await query.answer(f"{cmd_def.name}: {new_state}")
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"{new_state} — click to toggle", callback_data=f"tg:{cmd_def.name}:flip")]]
        )
        await safe_edit(
            query,
            f"*{cmd_def.name}*: {new_state}\nFlag: `{cmd_def.agy_flag}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=markup,
        )
        return

    await query.answer()


async def _handle_confirm_callback(query, data: str, session: dict):
    parts = data.split(":")
    if len(parts) < 3:
        await query.answer()
        return
    cmd_name = parts[1]
    answer = parts[2]
    cmd_def = resolve_command(cmd_name)
    if not cmd_def:
        await query.answer("Unknown command")
        return

    if answer == "no":
        await query.answer("Cancelled")
        await safe_edit(query, f"~~{cmd_def.name}~~ cancelled.", reply_markup=None)
        return

    if answer != "yes":
        await query.answer()
        return

    await query.answer("Confirmed")

    async def reply(text, markup):
        await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    await execute_command(reply, query.message.chat.id, cmd_def, session)


async def _handle_freetext_callback(query, data: str, session: dict):
    parts = data.split(":")
    if len(parts) < 3:
        await query.answer()
        return
    cmd_name = parts[1]
    if session.get("pending_text_input", {}).get("cmd_name") == cmd_name:
        session["pending_text_input"] = None
    await query.answer("Cancelled")
    await safe_edit(query, "Input cancelled.", reply_markup=None)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("Unauthorized access.")
        return

    session = get_session(update.effective_chat.id)
    user_text = update.message.text
    if not user_text:
        return

    # Consume pending free-text input
    pending = session.get("pending_text_input")
    if pending:
        if pending.get("expires_at", 0) < time.monotonic():
            session["pending_text_input"] = None
        else:
            cmd_name = pending["cmd_name"]
            session["pending_text_input"] = None
            cmd_def = resolve_command(cmd_name)
            if not cmd_def:
                await update.message.reply_text(f"Internal error: unknown pending command {cmd_name}")
                return

            async def reply(text, markup):
                if markup:
                    await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
                else:
                    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

            await execute_command(reply, update.effective_chat.id, cmd_def, session, value=user_text.strip())
            return

    # Normal agy prompt
    status_msg = await update.message.reply_text("⏳ Thinking…")
    cmd = build_session_agy_cmd(session, user_text)
    output = await run_agy_async(update.effective_chat.id, cmd)
    session["has_active_session"] = True
    try:
        await status_msg.delete()
    except Exception:
        pass
    await send_long_output(update.message, output)


async def post_init(app):
    commands = [BotCommand(name, desc) for name, desc in telegram_menu_commands()]
    try:
        await app.bot.set_my_commands(commands)
    except Exception as e:
        log.warning("set_my_commands failed: %s", e)


def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # One CommandHandler per registered command (and its aliases), all routed
    # through generic_command_entry. Adding a 41st command to the registry
    # needs zero new handler code.
    for cmd in COMMAND_REGISTRY:
        app.add_handler(CommandHandler([cmd.name, *cmd.aliases], generic_command_entry))

    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_message))

    log.info("Starting bridge with %d registered commands…", len(COMMAND_REGISTRY))
    app.run_polling()


if __name__ == "__main__":
    main()
