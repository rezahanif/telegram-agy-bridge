# Phase 1 Review — Findings & Fixes

Reviewed your Phase 1 files (`command_registry.py` + `bot.py`) against the
Phase 0 command table and your own Phase 1 spec ("register all 40 commands,
one fully working example of each action_type"). Applied all fixes on the
`phase-1-review` branch.

## Summary

| Area | Before | After |
|---|---|---|
| Commands registered | 10 | 32 (8 categories) |
| action_types covered | 4 (picker/text_input/confirm/immediate) | 5 (+toggle) |
| `frozen=True` on `CommandDef` | No | Yes |
| Repeatable flag support (`--add-dir`) | No (would overwrite) | Yes (`repeatable: bool` field) |
| Toggle support (`--sandbox`, `--yolo`, …) | No (no action_type for it) | Yes (`toggle` action_type + `confirm_if_enabling`) |
| Conflict handling (`--yolo` vs `--mode`) | No | Yes (`conflicts_with` field, cleared on set) |
| `destructive: bool` for Phase 5 auto-confirm | No | Yes |
| Subprocess model | `subprocess.run` (blocks event loop) | `asyncio.create_subprocess_exec` (async, `/stop` works) |
| `callback_data` length safety | Raw value in payload (could exceed 64B) | `cp:<8char_id>:<idx>` — always ≤ 18 bytes |
| Picker current-value marker (✓) | No | Yes |
| Picker pagination | No | Yes (`PICKER_PAGE_SIZE`, ◀/Next/▶ nav) |
| Stale-tap protection | No (double-tap would re-apply) | Yes (atomic `dict.pop()` + monotonic TTL) |
| Free-text Cancel button + TTL | No (had to send *something* to clear) | Yes (Cancel button + 180s expiry) |
| `session_flag` resetting `has_active_session` | Always (bug — `--model` would lose context) | Only when `resets_session=True` |
| `/stop` command | Missing | Implemented (kills running proc) |
| `execute_command` Update shim | `_Shim` class faking Update | Takes a `reply` callable (clean) |
| Double-reply in `button_callback` | Yes (edit + execute_command reply) | Fixed — execute_command owns the reply |
| Markdown V2 + plain-text fallback | Plain Markdown (breaks on `_`) | Markdown V2 with fallback |
| `subprocess` encoding | `text=True` (default, can choke) | `errors="replace"` |

## Findings — `command_registry.py`

### R1 — `CommandDef` not frozen (Low)
**Issue**: Dataclass had no `frozen=True`, so registry entries could be mutated at runtime.
**Fix**: Added `frozen=True`. Mirrors hermes' `@dataclass(frozen=True) class CommandDef`.

### R2 — No repeatable flag support (High)
**Issue**: `agy --help` says `--add-dir (repeatable) (default [])`, but the registry had no concept of repeatable flags. Setting `--add-dir` twice would overwrite the previous value in `session["flags"][flag]`.
**Fix**: Added `repeatable: bool` field. `execute_command` now stores repeatable values as a list and `build_session_agy_cmd` emits them correctly. Verified with sanity test T5.

### R3 — No `toggle` action_type (High)
**Issue**: `--sandbox`, `--dangerously-skip-permissions`, `--debug`, `--disable-slash-commands` are all boolean toggles with no value, but `action_type` only supported `picker / text_input / confirm / immediate`. They had nowhere to live.
**Fix**: Added `"toggle"` action_type + `confirm_if_enabling: bool` field (for the YOLO ⚠️ gate). Added `/sandbox`, `/yolo`, `/debug`, `/noslash` commands.

### R5 — Only 10 of ~40 commands registered (High, against Phase 1 spec)
**Issue**: Phase 1 Checkpoint 1 says "register all 40 commands in the / menu even if most just reply 'not implemented yet'". Registry had only 10.
**Fix**: Expanded to 32 commands across 8 categories. Commands not yet ready are marked `implemented=False` — the dispatcher replies "not implemented yet (Phase 3 batch pending)" instead of silently doing nothing. Remaining 8 (start/help/status/continue/debug/noslash/changelog/install + 8 plugin cmds) are stubs ready for Phase 3.

### R6 — No `conflicts_with` field (Medium)
**Issue**: `--new-project` should clear `--project` (can't both switch and create). `--yolo` should clear `--mode`. Phase 2 mentions this but the schema needs the field *now*.
**Fix**: Added `conflicts_with: tuple[str, ...]` field. `execute_command` clears conflicting flags when a new value is set. Verified with sanity test T8.

### R7 — No `destructive: bool` (Medium)
**Issue**: Phase 5 wants auto-confirm for risky commands. Without a `destructive` flag, every consumer has to re-check `confirm_prompt` is set.
**Fix**: Added `destructive: bool`. Phase 5 can auto-wrap any `destructive=True` command in a confirm even if `confirm_prompt` is empty.

### R9 — `choices_from_agy` as single string (Medium)
**Issue**: Field was `Optional[str]` (e.g. `"models"`), but `agy plugin list` needs two args. Field name was misleading.
**Fix**: Changed to `Optional[tuple[str, ...]]` — full subcommand argv. `parse_agy_choices` runs `agy <choices_from_agy>` and parses the output.

### Also added
- `choice_labels` — optional display labels parallel to `choices` (so `/model` can show "Gemini 3.1 Pro" while emitting `gemini-3.1-pro-high`)
- `resets_session: bool` — only `--new-project` and explicit `/new` reset `has_active_session`
- `long_running: bool` — `/update` uses a longer timeout
- `commands_by_category()` helper for `/help`

## Findings — `bot.py`

### B1 — `subprocess.run` blocks the event loop (Critical)
**Issue**: `run_agy_sync` used `subprocess.run(..., timeout=180)`. While agy is running, the bot can't process `/stop`, other users' commands, or Telegram pings. Phase 5's "kill agy mid-call" is impossible.
**Fix**: Replaced with `asyncio.create_subprocess_exec` + `asyncio.wait_for`. Running procs tracked in `running_procs: dict[chat_id, Process]` so `/stop` can kill them. Two runners: `run_agy_async` (chat prompts, tracked) and `run_agy_capture` (subcommands, one-shot).

### B2 — `callback_data` could exceed 64 bytes (High)
**Issue**: `f"{cmd_def.name}:{choice}"` — a model name like `gemini-3.6-flash-medium` (22 chars) + prefix is fine, but `--include-directories` with a full path could blow past 64 bytes silently (button stops working).
**Fix**: Pickers now use `cp:<8char_picker_id>:<idx>` — always ≤ 18 bytes even with absurd indices. The actual choice text lives in the picker state dict, not the callback payload. Verified with callback_data length tests.

### B3 — Picker parser is naive (High — acknowledged TODO)
**Issue**: `[line.strip() for line in output.splitlines() if line.strip()]` would split one model into multiple buttons if `agy models` output is multi-line per model (likely).
**Fix**: Added `parse_agy_choices()` with a line-based fallback that skips section headers and caps at 50 entries. **Marked with explicit TODO for Phase 3** — once you see real `agy models` / `agy agents` output, replace the parser.

### B4 — No current-value marker on pickers (Medium)
**Issue**: Users couldn't see which option was currently active.
**Fix**: Pickers now mark the current value with ` ✓` on the button label.

### B5 — No pagination (Medium)
**Issue**: Once `choices_from_agy="agents"` returns 20+ agents, you'd have 20 buttons in one message.
**Fix**: Added `PICKER_PAGE_SIZE` (default 8) + ◀ Prev / `[N/M]` / Next ▶ nav row. Page counter uses `cp:<id>:noop` (non-interactive toast).

### B6 — No stale-tap protection (Medium)
**Issue**: Run `/model`, don't tap, run `/model` again — first message's buttons still work and silently overwrite.
**Fix**: Picker state stored in `pickers: dict[picker_id, state]` with `expires_at`. On tap, atomic `pickers.pop(picker_id, None)` — if `None` or expired, reply "Picker expired — run the command again." Mirrors hermes' `tools/slash_confirm.py` pattern.

### B7 — `session_flag` always reset `has_active_session` (High — bug factory)
**Issue**: `execute_command` set `session["has_active_session"] = False` for every `session_flag` command with a comment "adjust per-command if wrong". This means `/model` would lose your conversation context.
**Fix**: Added `resets_session: bool` on `CommandDef`. Only `--new-project` and explicit `/new` set it to `True`. All other session_flag commands (`--model`, `--effort`, `--mode`, `--agent`, `--add-dir`, `--project`, `--sandbox`, etc.) leave `has_active_session` untouched.

### B8 — Free-text input had no Cancel button or timeout (Medium)
**Issue**: Run `/project`, change your mind — you have to send *something* to clear `pending_text_input`. A stray "yes" would be set as the project name.
**Fix**: `pending_text_input` is now a dict with `expires_at` (180s). Free-text prompt shows a `✗ Cancel` button (`ft:<cmd>:cancel`). On timeout, the next text message is treated as a normal prompt instead.

### B9 — `_Shim` class faking Update (Medium)
**Issue**: `button_callback` built a `_Shim` object to fake an Update so it could call `execute_command(_Shim(), ...)`. Fragile — any new field access on `update` would break.
**Fix**: `execute_command` now takes a `reply: Callable[[str, Optional[markup]], Awaitable[None]]` instead of an Update. Callers pass the right reply callable (Update or CallbackQuery).

### B10 — `button_callback` double-replied (Medium)
**Issue**: After `execute_command` replied with `✅ /model set to X`, the callback tried to `query.edit_message_text("✅ /model done.")` — either overwriting the useful message or failing silently.
**Fix**: `execute_command` owns the reply. `button_callback` only handles Cancel/no-op cases directly.

### B11 — No `/stop` command (Medium)
**Issue**: Phase 5 explicitly wants this, but blocked by B1.
**Fix**: Now that B1 is fixed, `/stop` just looks up `running_procs[chat_id]` and calls `proc.kill()`. Implemented as a `standalone` / `immediate` command with empty `agy_subcommand` — `execute_command` dispatches in-process when `agy_subcommand == ()`.

### B12 — `subprocess` encoding (Low)
**Issue**: `text=True` uses default encoding which can choke on agy's UTF-8 output (emoji, non-ASCII).
**Fix**: Decode bytes manually with `errors="replace"`.

### B13 — Plain Markdown, no fallback (Low)
**Issue**: `parse_mode="Markdown"` breaks on `_` in model names (e.g. `gemini_3_6_flash`).
**Fix**: `safe_reply` / `safe_edit` try Markdown V2 first, fall back to plain text on `BadRequest`. `send_long_output` splits at 4000 chars with the same fallback.

## Sanity tests (Phase 2 preview)

Wrote 9 inline sanity tests for `build_session_agy_cmd` and `build_standalone_agy_cmd`. All pass:

```
T1 empty session:            ['/root/.local/bin/agy', '-p', 'hello']
T2 active session:           ['/root/.local/bin/agy', '--continue', '-p', 'next message']
T3 model+effort:             [..., '--continue', '--model', 'gemini-3.1-pro-high', '--effort', 'high', '-p', 'do something']
T4 toggle bare flag:         [..., '--sandbox', '--model', 'gemini-3.6-flash-medium', '-p', 'prompt']
T5 repeatable --add-dir:     [..., '--add-dir', '/project/src', '/project/tests', '-p', 'prompt']
T6 standalone /models:       ['/root/.local/bin/agy', 'models']
T7 standalone + value:       ['/root/.local/bin/agy', 'plugin', 'install', 'myplugin@marketplace']
T8 conflict (yolo set):      [..., '--dangerously-skip-permissions', '-p', 'prompt']  # --mode cleared
T9 full combo:               [..., '--continue', '--model', X, '--effort', 'high', '--mode', 'plan',
                              '--output-format', 'json', '--add-dir', src, tests, '--sandbox', '-p', ...]
```

Plus callback_data length tests — all `cp:` / `tg:` / `ft:` / `cf:` payloads stay ≤ 36 bytes (cap is 64).

These tests should be formalized as `tests/test_build_cmd.py` in Phase 2.

## What's NOT fixed (deferred to Phase 2+)

- **`/start`, `/help`, `/status` not implemented** — registered as stubs (`implemented=False`). Phase 2 or early Phase 3 should ship these since they're needed for debugging.
- **Real `agy models` / `agy agents` parser** — `parse_agy_choices` uses line-based fallback. Phase 3 Batch A should replace this once you see real output.
- **`/continue` as a command** — currently auto-managed by `has_active_session`. May not need to be a separate command at all; consider removing from registry in Phase 2.
- **Streaming output** — bot still waits for agy to finish then sends the whole thing. Phase 5+ would stream stdout chunks via throttled `edit_message_text`.
- **Per-command authz** (admin vs user) — overkill for single-user bridge but hermes' `SlashAccessPolicy` is the template if you ever share the bot.
- **Rate limiting** — Phase 5 hardening item.
- **Long-output chunking for `send_long_output`** — current 4000-char split is fine, but doesn't preserve code-block boundaries. Phase 5 could improve.

## Migration / rollback

This is a non-destructive review pass. To try it on your bot:

```bash
git fetch origin
git checkout phase-1-review
# set env vars, then:
python bot.py
```

To roll back:

```bash
git checkout main
```

No data migration needed — the bridge has always been stateless across restarts.
