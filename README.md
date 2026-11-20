# `agy-config` — Reusable Antigravity CLI Setup, Plugins & `agy-sessions`

Standalone repository containing shared Antigravity CLI (`agy`) settings, custom status line, global agent rules, and the `agy-sessions` session management tool & hook across machines.

## Repository Layout

```text
agy-config/
├── setup.sh                              # Bootstrap / sync script for any machine
├── cli/
│   ├── settings.base.json                # Shared agentMode, sandbox, git allow/ask/deny, statusLine
│   ├── statusline.sh                     # Symlinked to ~/.gemini/antigravity-cli/statusline.sh
│   └── machines/
│       └── WE50969.json                  # Per-host overrides (e.g., trustedWorkspaces)
├── tools/
│   └── agy-sessions/                     # Python 3.11+ stdlib CLI (symlinked to ~/.local/bin/agy-sessions)
│       ├── agy-sessions                  # Executable entry point
│       └── agy_sessions/                 # config, index, dump, delete, cli
└── plugins/
    └── personal-kit/                     # Global Antigravity plugin (registered via ~/.gemini/config/plugins.json)
        ├── plugin.json                   # Plugin manifest
        ├── hooks.json                    # SessionStart, PreInvocation, and Stop hooks
        ├── update_sessions.py            # Fail-open hook shim invoking tools/agy-sessions
        └── rules/
            └── AGENTS.md                 # Global git commit/push approval & sandbox rules
```

## Setup on Any Machine

1. Clone this repository (e.g., `~/agy-config`) and ensure `jq` and Python 3.11+ (`python3`) are installed.
2. *(Optional)* Add machine-specific overrides (such as `trustedWorkspaces`) in `cli/machines/$(hostname -s).json`.
3. Run:
   ```bash
   ~/agy-config/setup.sh
   ```

What `setup.sh` configures:
- Symlinks `cli/statusline.sh` → `~/.gemini/antigravity-cli/statusline.sh`
- Symlinks `tools/agy-sessions/agy-sessions` → `~/.local/bin/agy-sessions`
- Merges `cli/settings.base.json` + `cli/machines/<hostname>.json` into `~/.gemini/antigravity-cli/settings.json` (preserving any existing keys and deduplicating permission/workspace lists)
- Registers `<repo>/plugins` (by absolute path) in `~/.gemini/config/plugins.json` so `git pull` updates hooks and global rules live across all projects

## Per-Project Session Management (`agy-sessions`)

Modeled after `claude_sessions` (Python 3.11+ stdlib only, no third-party dependencies). Keeps track of a project's Antigravity CLI sessions:

- **index**: auto-generated Markdown index (`agy/{host}/sessions.md` by default) grouped by rules in `.agy-sessions.toml`, with dates, titles, prompt counts, session IDs, transcript dump links, a hand-off notes table, zero-prompt session listing, and config checks — rebuilt automatically by the `personal-kit` hooks.
- **dump**: converts a session's `transcript_full.jsonl` (or `transcript.jsonl`) to readable Markdown under `agy/{host}/transcripts/`. *(Unlike Claude Code, `agy` does not currently expose `PreCompact`/`SessionEnd` hook events, so dumps are run on demand via `agy-sessions dump`.)*
- **delete**: removes selected sessions (by `--group`, `--group empty`, or ID prefix) across `~/.gemini/antigravity-cli/` (`conversations/<ID>.db*`, `brain/<ID>/`, `annotations/<ID>.pbtxt`, `presence/<ID>.lock`, and `conversation_summaries.db` rows), skipping any live/running session (detected via `presence/<ID>.lock`) and leaving Markdown dumps and `history.jsonl` intact.

### Opting a Project In

Inside any git repository:

```bash
cd /path/to/project
agy-sessions init
```

This creates `.agy-sessions.toml` (commit it), creates `agy/<host>/`, `agy/<host>/transcripts/`, and `agy/handoffs/`, writes the initial `agy/<host>/sessions.md` (including prior local sessions), and adds `agy/*/sessions.md` and `agy/*/transcripts/` to `.gitignore`.

*(Projects with an existing `agy/` folder but no `.agy-sessions.toml` continue to be indexed with default paths and no groups until you run `agy-sessions init`.)*

### `.agy-sessions.toml`

```toml
[paths]           # relative to this file; {host} = short hostname; "" disables
index = "agy/{host}/sessions.md"
transcripts = "agy/{host}/transcripts"
handoffs = "agy/handoffs"

[[group]]
key = "setup"
title = "Project setup"
names = ["setup"]        # optional substring match on title (case/space/punct ignored)
ids = []                 # optional 8-char session ID prefixes
```

Grouping priority:
1. A group whose `ids` contains the session's 8-char ID prefix.
2. A group whose `key` prefixes the session title (`setup: part 2` → `setup`, case-insensitive).
3. The first group in file order with a `names` substring matching the session title.

Unclaimed sessions appear under **Ungrouped**; zero-prompt sessions appear under **Without prompts**; stale group entries and unknown `<word>: ...` title prefixes appear under **Checks**.

### Commands

| Command | Does |
| :--- | :--- |
| `agy-sessions init [DIR]` | Create `.agy-sessions.toml`, `agy/` dirs, initial `sessions.md`, and `.gitignore` entries |
| `agy-sessions index [DIR] [--stdout]` | Rebuild `sessions.md` (or print it), showing counts and checks |
| `agy-sessions dump SESSION [LABEL]` | Dump a session transcript (`.jsonl` path or ID prefix) to Markdown |
| `agy-sessions delete [-n] [-y] [-g KEY]... [ID]...` | Delete sessions by group (`empty`, `junk`, etc.) or ID prefix (`-n` dry run, `-y` no prompt) |
| `agy-sessions hook` | Lifecycle hook entrypoint invoked by `personal-kit` |
