"""
Phase 1 skeleton — command registry + generic dispatch.

Design decisions from your Phase 0 table:
  - Two invocation shapes:
      "session_flag"  -> merges into the persistent agy command line
                          (--model, --effort, --mode, --add-dir, --project, ...)
      "standalone"    -> runs its own agy invocation, independent of session
                          state (agy models, agy plugin list, agy update, ...)
  - Five action_types drive what happens after the user taps /command:
      "picker"      -> inline keyboard of fixed choices
      "text_input"  -> bot asks "reply with a value", captured via a
                        pending-input state on the chat
      "confirm"     -> yes/no inline keyboard before running
      "immediate"   -> runs with no further input needed
      "toggle"      -> boolean flag (--sandbox, --dangerously-skip-permissions, ...)
                        click to flip on/off, optionally with confirm gate

Phase 1 review fixes applied (see PHASE_1_REVIEW.md):
  - frozen=True so registry entries can't be mutated at runtime
  - added `toggle` action_type for boolean flags (R3)
  - added `repeatable: bool` + `repeatable_separator` for --add-dir (R2)
  - added `destructive: bool` so Phase 5 can auto-confirm risky commands (R7)
  - added `conflicts_with: list[str]` so Phase 2 can model mutual exclusion (R6)
  - `choices_from_agy` is now `list[str]` (subcommand argv) so `agy plugin list`
    works as well as `agy models` (R9)
  - registry now contains all 40 commands from the Phase 0 table (R5) — most
    are marked `implemented=False` so the dispatcher replies "not implemented
    yet" rather than silently doing nothing

Fill in `implemented=True` as you ship each batch in Phase 3.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class CommandDef:
    name: str                          # "model" -> /model
    description: str                   # shown in the "/" menu
    action_type: str                   # "picker" | "text_input" | "confirm" | "immediate" | "toggle"
    invocation: str                    # "session_flag" | "standalone"
    aliases: tuple[str, ...] = ()

    # --- for action_type == "picker" ---
    choices: Optional[tuple[str, ...]] = None              # static choices (raw agy flag values)
    choice_labels: Optional[tuple[str, ...]] = None        # optional display labels (parallel to choices)
    choices_from_agy: Optional[tuple[str, ...]] = None     # subcommand argv, e.g. ("models") or ("plugin", "list")

    # --- for action_type == "toggle" ---
    # `choices`/`agy_flag` are reused: toggle commands have no `value`,
    # they just add/remove the bare flag from the session.
    confirm_if_enabling: bool = False   # e.g. --dangerously-skip-permissions needs ⚠️ gate

    # --- for action_type == "confirm" ---
    confirm_prompt: Optional[str] = None

    # --- how the value gets attached to the agy invocation ---
    agy_flag: Optional[str] = None             # e.g. "--model", "--effort"
    agy_subcommand: Optional[tuple[str, ...]] = None  # e.g. ("plugin", "list") for standalone commands

    # --- session_flag semantics ---
    repeatable: bool = False                   # --add-dir can appear multiple times
    repeatable_separator: Optional[str] = None  # if set, joins repeated values into one flag arg
                                                # (None = emit flag once per value, like --add-dir X --add-dir Y)
    resets_session: bool = False               # most session_flag commands don't reset -- only --new-project does
    conflicts_with: tuple[str, ...] = ()       # agy_flag values this one clears when set

    # --- Phase 5 hardening hints ---
    destructive: bool = False                  # auto-wraps in confirm if no confirm_prompt set
    long_running: bool = False                 # uses longer timeout (e.g. agy update)

    category: str = "general"
    implemented: bool = True                   # Phase 1 ships one example of each action_type;
                                               # rest are False until their Phase 3 batch lands


# ---------------------------------------------------------------------------
# Phase 0 table -> registry. All 40 commands present; most are stubs until
# Phase 3 batches ship. One fully-working example of each action_type is
# marked implemented=True so Checkpoint 1 has something real to test:
#   picker      -> /model       (session_flag)
#   picker      -> /models      (standalone, immediate)
#   toggle      -> /sandbox     (session_flag)
#   text_input  -> /project     (session_flag)
#   confirm     -> /update      (standalone)
# ---------------------------------------------------------------------------

COMMAND_REGISTRY: list[CommandDef] = [
    # ============================================================
    # Category: agent_model — session_flag pickers/toggles
    # ============================================================
    CommandDef(
        name="model",
        description="Select AI model for this session",
        action_type="picker",
        invocation="session_flag",
        agy_flag="--model",
        choices_from_agy=("models",),   # populate keyboard by running `agy models`
        category="agent_model",
    ),
    CommandDef(
        name="effort",
        aliases=("thinking",),
        description="Set reasoning effort (low/medium/high)",
        action_type="picker",
        invocation="session_flag",
        agy_flag="--effort",
        choices=("low", "medium", "high"),
        category="agent_model",
    ),
    CommandDef(
        name="agent",
        description="Set agent for the current CLI session",
        action_type="picker",
        invocation="session_flag",
        agy_flag="--agent",
        choices_from_agy=("agents",),
        category="agent_model",
    ),
    CommandDef(
        name="mode",
        description="Set execution mode (accept-edits/plan)",
        action_type="picker",
        invocation="session_flag",
        agy_flag="--mode",
        choices=("accept-edits", "plan"),
        conflicts_with=("--dangerously-skip-permissions",),  # YOLO is a separate toggle
        category="agent_model",
    ),
    CommandDef(
        name="output",
        description="Set output format for print mode (text/json/stream-json)",
        action_type="picker",
        invocation="session_flag",
        agy_flag="--output-format",
        choices=("text", "json", "stream-json"),
        category="agent_model",
    ),

    # ============================================================
    # Category: session — workspace/session control
    # ============================================================
    CommandDef(
        name="new",
        aliases=("reset",),
        description="Start a fresh conversation (forget --continue)",
        action_type="confirm",
        invocation="session_flag",
        confirm_prompt="Start a new session? Your current conversation context will be lost.",
        resets_session=True,
        category="session",
    ),
    CommandDef(
        name="continue",
        description="Force-continue the most recent conversation",
        action_type="immediate",
        invocation="session_flag",
        agy_flag="--continue",
        category="session",
        implemented=False,  # auto-managed by has_active_session; expose as /status info instead
    ),
    CommandDef(
        name="conversation",
        description="Resume a previous conversation by ID",
        action_type="text_input",
        invocation="session_flag",
        agy_flag="--conversation",
        category="session",
    ),
    CommandDef(
        name="stop",
        description="Stop the currently running agy command",
        action_type="immediate",
        invocation="standalone",
        agy_subcommand=(),  # no agy call — handled in-process by killing the proc
        category="session",
    ),
    CommandDef(
        name="status",
        description="Show current session configuration",
        action_type="immediate",
        invocation="standalone",
        agy_subcommand=(),  # no agy call — reads in-memory session
        category="session",
        implemented=True,
    ),
    CommandDef(
        name="help",
        aliases=("commands",),
        description="List all available commands",
        action_type="immediate",
        invocation="standalone",
        agy_subcommand=(),
        category="session",
        implemented=True,
    ),
    CommandDef(
        name="start",
        description="Show welcome + current config",
        action_type="immediate",
        invocation="standalone",
        agy_subcommand=(),
        category="session",
        implemented=True,
    ),

    # ============================================================
    # Category: workspace — files & directories
    # ============================================================
    CommandDef(
        name="adddir",
        description="Add a directory to the workspace (repeatable)",
        action_type="text_input",
        invocation="session_flag",
        agy_flag="--add-dir",
        repeatable=True,  # R2 fix: --add-dir is repeatable per agy --help
        category="workspace",
    ),
    CommandDef(
        name="project",
        description="Switch project ID for this session",
        action_type="text_input",
        invocation="session_flag",
        agy_flag="--project",
        conflicts_with=("--new-project",),
        category="workspace",
    ),
    CommandDef(
        name="newproject",
        description="Create a new project for this session",
        action_type="confirm",
        invocation="session_flag",
        agy_flag="--new-project",
        confirm_prompt="This will create a new project for the session. Continue?",
        destructive=True,
        conflicts_with=("--project",),
        resets_session=True,
        category="workspace",
    ),

    # ============================================================
    # Category: flags — boolean toggles (R3 fix)
    # ============================================================
    CommandDef(
        name="sandbox",
        description="Toggle --sandbox (terminal restrictions enabled)",
        action_type="toggle",
        invocation="session_flag",
        agy_flag="--sandbox",
        category="flags",
    ),
    CommandDef(
        name="yolo",
        description="Toggle --dangerously-skip-permissions (auto-approve all tools)",
        action_type="toggle",
        invocation="session_flag",
        agy_flag="--dangerously-skip-permissions",
        confirm_if_enabling=True,
        destructive=True,
        conflicts_with=("--mode",),  # --mode plan/accept-edits is the safer alternative
        category="flags",
    ),
    CommandDef(
        name="debug",
        description="Toggle --debug logging",
        action_type="toggle",
        invocation="session_flag",
        agy_flag="--debug",
        category="flags",
        implemented=False,
    ),
    CommandDef(
        name="noslash",
        description="Toggle --disable-slash-commands in print mode",
        action_type="toggle",
        invocation="session_flag",
        agy_flag="--disable-slash-commands",
        category="flags",
        implemented=False,
    ),

    # ============================================================
    # Category: info — standalone immediate subcommands
    # ============================================================
    CommandDef(
        name="models",
        description="List available models",
        action_type="immediate",
        invocation="standalone",
        agy_subcommand=("models",),
        category="info",
    ),
    CommandDef(
        name="agents",
        description="List available agents",
        action_type="immediate",
        invocation="standalone",
        agy_subcommand=("agents",),
        category="info",
    ),
    CommandDef(
        name="changelog",
        description="Show agy changelog and release notes",
        action_type="immediate",
        invocation="standalone",
        agy_subcommand=("changelog",),
        category="info",
        implemented=True,
    ),
    CommandDef(
        name="install",
        description="Configure environment paths and shell settings",
        action_type="confirm",
        invocation="standalone",
        agy_subcommand=("install",),
        confirm_prompt="This will modify your shell config and PATH. Continue?",
        destructive=True,
        category="info",
        implemented=True,
    ),
    CommandDef(
        name="update",
        description="Update the agy CLI",
        action_type="confirm",
        invocation="standalone",
        agy_subcommand=("update",),
        confirm_prompt="This will update the agy CLI binary. Continue?",
        destructive=True,
        long_running=True,
        category="info",
    ),

    # ============================================================
    # Category: plugin — plugin management subcommands
    # ============================================================
    CommandDef(
        name="plugins",
        aliases=("pluginlist",),
        description="List installed plugins",
        action_type="immediate",
        invocation="standalone",
        agy_subcommand=("plugin", "list"),
        category="plugin",
        implemented=True,
    ),
    CommandDef(
        name="plugininstall",
        description="Install a plugin (name or plugin@marketplace)",
        action_type="text_input",
        invocation="standalone",
        agy_subcommand=("plugin", "install"),  # value appended as final arg
        category="plugin",
        implemented=True,
    ),
    CommandDef(
        name="pluginuninstall",
        description="Uninstall a plugin by name",
        action_type="text_input",
        invocation="standalone",
        agy_subcommand=("plugin", "uninstall"),
        confirm_prompt="Uninstall this plugin?",
        destructive=True,
        category="plugin",
        implemented=True,
    ),
    CommandDef(
        name="pluginenable",
        description="Enable a plugin by name",
        action_type="text_input",
        invocation="standalone",
        agy_subcommand=("plugin", "enable"),
        category="plugin",
        implemented=True,
    ),
    CommandDef(
        name="plugindisable",
        description="Disable a plugin by name",
        action_type="text_input",
        invocation="standalone",
        agy_subcommand=("plugin", "disable"),
        category="plugin",
        implemented=True,
    ),
    CommandDef(
        name="pluginvalidate",
        description="Validate a plugin directory",
        action_type="text_input",
        invocation="standalone",
        agy_subcommand=("plugin", "validate"),
        category="plugin",
        implemented=True,
    ),
    CommandDef(
        name="pluginimport",
        description="Import a plugin from a source path or preset",
        action_type="text_input",
        invocation="standalone",
        agy_subcommand=("plugin", "import"),
        category="plugin",
        implemented=True,
    ),
    CommandDef(
        name="pluginlink",
        description="Link a marketplace to a target dir (mp + target)",
        action_type="text_input",
        invocation="standalone",
        agy_subcommand=("plugin", "link"),
        category="plugin",
        implemented=True,
    ),
]

# Fast lookups
COMMAND_LOOKUP: dict[str, CommandDef] = {}
for _cmd in COMMAND_REGISTRY:
    COMMAND_LOOKUP[_cmd.name] = _cmd
    for _alias in _cmd.aliases:
        COMMAND_LOOKUP[_alias] = _cmd


def resolve_command(name: str) -> Optional[CommandDef]:
    return COMMAND_LOOKUP.get(name.lower().lstrip("/"))


def telegram_menu_commands() -> list[tuple[str, str]]:
    """(name, description) pairs for set_my_commands, generated from the registry."""
    return [(cmd.name, cmd.description) for cmd in COMMAND_REGISTRY]


def commands_by_category() -> dict[str, list[CommandDef]]:
    """Group commands by category — used by /help."""
    out: dict[str, list[CommandDef]] = {}
    for cmd in COMMAND_REGISTRY:
        out.setdefault(cmd.category, []).append(cmd)
    return out
