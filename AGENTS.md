# Repository Guidelines (`agy-config`)

Shared Antigravity CLI (`agy`) configuration, custom status line, and the global `personal-kit` plugin synchronized across machines.

## Rule Scope: Root vs. Global Plugin

- **`AGENTS.md` (this file):** Applies only when working inside the `agy-config` repository.
- **`plugins/personal-kit/rules/AGENTS.md`:** Loaded **globally across every workspace** on any machine where `setup.sh` has been run. Keep global rules minimal, universally applicable, and mindful of the shared always-on rules token budget; place repo-specific guidance here instead.
- Standalone `AGENTS.md` files in Antigravity do **not** use YAML frontmatter.

## Architecture & How Changes Take Effect

| Path | Role | How updates apply |
| :--- | :--- | :--- |
| `cli/statusline.sh` | ANSI status line script (`sh` + `jq`) reading JSON from `stdin` | **Live** via symlink at `~/.gemini/antigravity-cli/statusline.sh` |
| `plugins/personal-kit/` | Native global plugin (`plugin.json`, `hooks.json`, `update_sessions.py`, `rules/AGENTS.md`) | **Live** via `~/.gemini/config/plugins.json` entry pointing to `<repo>/plugins` (`update_sessions.py` runs fresh per event; restart `agy` to reload `plugin.json`/`hooks.json`/rules) |
| `cli/settings.base.json` | Cross-machine defaults (`agentMode`, sandbox, git permissions, `statusLine`) | **Requires `./setup.sh`** (merged into `~/.gemini/antigravity-cli/settings.json`, never symlinked because `agy` atomically rewrites `settings.json` on `/config` saves) |
| `cli/machines/<host>.json` | Per-machine overrides keyed by short hostname (`hostname -s`, e.g., `WE50969.json`) | **Requires `./setup.sh`** (overlaid after `settings.base.json`) |
| `setup.sh` | Idempotent bootstrap/sync script (`--gemini-dir <path>` supported for isolated testing) | Run directly; writing to real `~/.gemini/` requires `BypassSandbox: true` when the terminal sandbox is active |

### Settings Merge Behavior (`setup.sh`)

- Merge order: existing `~/.gemini/antigravity-cli/settings.json` ← `cli/settings.base.json` ← `cli/machines/<short-hostname>.json`.
- `trustedWorkspaces` and list-valued `permissions.*` (`allow`, `ask`, `deny`) are **additively concatenated and deduplicated**; dict keys shallow-merge; scalar keys overwrite.
- Removing an entry from `settings.base.json` or `machines/<host>.json` will **not** delete it from an already-populated `~/.gemini/antigravity-cli/settings.json`—clean it up in both places when revoking a permission or workspace.
- Never place machine-local filesystem paths (such as `trustedWorkspaces`) in `cli/settings.base.json`; put them in `cli/machines/$(hostname -s).json`.

## Component Conventions & Safety Invariants

- **Dependencies & Portability:** Stick strictly to POSIX `sh`/`bash`, `jq`, and the Python 3 standard library (no third-party `pip` packages) so new machines bootstrap cleanly. Invoke Python scripts with `python3 -B` to avoid writing `__pycache__/` into the live plugin directory. Keep `cli/statusline.sh` and `plugins/personal-kit/update_sessions.py` executable (`chmod +x`).
- **Native Plugin Layout (`plugins/<name>/`):** Plugins registered via `plugins.json` do not run extension staging—`plugin.json` and `hooks.json` **must** stay directly at `plugins/<name>/` root (never nested under `hooks/hooks.json`), with consolidated rules in `plugins/<name>/rules/AGENTS.md`.
- **Lifecycle Hooks (`hooks.json` & `update_sessions.py`):**
  - Hooks execute synchronously and block the agent loop across **all** projects (`timeout: 5` seconds, working directory set to `plugins/personal-kit/`). Keep execution fast and side-effect-safe.
  - Hook `stdin` payloads use **camelCase** keys (`conversationId`, `workspacePaths`, `transcriptPath`, `hookEventName`, `cwd`).
  - **Fail open:** Always catch unexpected exceptions, exit `0`, and print valid JSON to `stdout` (`{"decision": "allow"}` when `hookEventName == "Stop"`, `{}` otherwise) so hook errors never stall or break `agy`.
- **Session Indexer (`update_sessions.py`):**
  - **Opt-in only:** Only create or update `<project>/agy/<short-hostname>/sessions.md` when `<project>/agy/` already exists (walking up from `cwd` / `workspacePaths` and stopping at `.git`, `$HOME`, or `/`).
  - Syncs all non-empty top-level sessions for the project (and their titles) read-only from `conversation_summaries.db` + O(1) head/tail scans of `transcript.jsonl`, skipping zero-turn sessions and subagents.
  - `sessions.md` is fully auto-generated (not manually edited) and written atomically (`tempfile.mkstemp` + `os.replace`) only when content changes. Supports `--sync <project_dir>` for manual one-off backfill.

## Verification (Sandboxed)

All validation checks can run inside the standard terminal sandbox (`BypassSandbox: false`):

```bash
# 1. Syntax & JSON validation
bash -n setup.sh && sh -n cli/statusline.sh && \
  python3 -B -c "import ast; ast.parse(open('plugins/personal-kit/update_sessions.py').read())" && \
  jq empty cli/settings.base.json cli/machines/*.json plugins/personal-kit/plugin.json plugins/personal-kit/hooks.json

# 2. Smoke-test statusline.sh
printf '{"model":{"display_name":"Gemini"},"cycle_mode":"accept-edits","conversation_title":"Test","context_window":{"used_percentage":42}}' | ./cli/statusline.sh

# 3. Smoke-test setup.sh in an isolated temp directory
tmp_dir="$(mktemp -d)" && ./setup.sh --gemini-dir "$tmp_dir" && rm -rf "$tmp_dir"
```
