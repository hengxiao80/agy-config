# `agy-config` — Reusable Antigravity CLI Setup & Plugins

Standalone repository containing shared Antigravity CLI (`agy`) settings, custom status line, global agent rules, and opt-in per-project session tracking across machines.

## Repository Layout

```text
agy-config/
├── setup.sh                        # Bootstrap / sync script for any machine
├── cli/
│   ├── settings.base.json          # Shared agentMode, sandbox, git allow/ask/deny, statusLine
│   ├── statusline.sh               # Symlinked to ~/.gemini/antigravity-cli/statusline.sh
│   └── machines/
│       └── WE50969.json            # Per-host overrides (e.g., trustedWorkspaces)
└── plugins/
    └── personal-kit/               # Global Antigravity plugin (registered via ~/.gemini/config/plugins.json)
        ├── plugin.json             # Plugin manifest
        ├── hooks.json              # SessionStart, PreInvocation, and Stop hooks
        ├── update_sessions.py      # Maintains <project>/agy/<hostname>/sessions.md (opt-in)
        └── rules/
            └── AGENTS.md           # Global git commit/push approval & sandbox rules
```

## Setup on Any Machine

1. Clone this repository (e.g., `~/agy-config`) and ensure `jq` and `python3` are installed.
2. *(Optional)* Add machine-specific overrides (such as `trustedWorkspaces`) in `cli/machines/$(hostname -s).json`.
3. Run:
   ```bash
   ~/agy-config/setup.sh
   ```

What `setup.sh` configures:
- Symlinks `cli/statusline.sh` → `~/.gemini/antigravity-cli/statusline.sh`
- Merges `cli/settings.base.json` + `cli/machines/<hostname>.json` into `~/.gemini/antigravity-cli/settings.json` (preserving any existing keys and deduplicating permission/workspace lists)
- Registers `<repo>/plugins` (by absolute path) in `~/.gemini/config/plugins.json` so `git pull` updates hooks and global rules live across all projects

## Opt-In Per-Project Session Tracking

The `personal-kit` plugin's lifecycle hook automatically maintains `<project>/agy/<hostname>/sessions.md` **only for projects that opt in** by having an `agy/` directory at the project root:

```bash
mkdir -p /path/to/project/agy
```

Projects without an `agy/` directory are untouched. `sessions.md` is fully auto-generated from Antigravity's local session database (`conversation_summaries.db`) and transcripts — including session titles and retroactive backfill — so it should not be edited manually. You can also trigger a one-off backfill with:

```bash
python3 -B ~/agy-config/plugins/personal-kit/update_sessions.py --sync /path/to/project
```
