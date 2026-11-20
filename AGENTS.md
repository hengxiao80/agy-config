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
| `tools/agy-sessions/` | Python 3.11+ stdlib CLI (`init`, `index`, `dump`, `delete`, `hook`) | **Live** via symlink at `~/.local/bin/agy-sessions` (created by `./setup.sh`) and imported by `plugins/personal-kit/update_sessions.py` |
| `plugins/personal-kit/` | Native global plugin (`plugin.json`, `hooks.json`, `update_sessions.py`, `rules/AGENTS.md`) | **Live** via `~/.gemini/config/plugins.json` entry pointing to `<repo>/plugins` (`update_sessions.py` runs fresh per event; restart `agy` to reload `plugin.json`/`hooks.json`/rules) |
| `cli/settings.base.json` | Cross-machine defaults (`agentMode`, sandbox, git permissions, `statusLine`) | **Requires `./setup.sh`** (merged into `~/.gemini/antigravity-cli/settings.json`, never symlinked because `agy` atomically rewrites `settings.json` on `/config` saves) |
| `cli/machines/<host>.json` | Per-machine overrides keyed by short hostname (`hostname -s`, e.g., `WE50969.json`) | **Requires `./setup.sh`** (overlaid after `settings.base.json`) |
| `setup.sh` | Idempotent bootstrap/sync script (`--gemini-dir <path>` and `--bin-dir <path>` supported for isolated testing) | Run directly; writing to real `~/.gemini/` / `~/.local/bin/` requires `BypassSandbox: true` when the terminal sandbox is active |

### Settings Merge Behavior (`setup.sh`)

- Merge order: existing `~/.gemini/antigravity-cli/settings.json` ← `cli/settings.base.json` ← `cli/machines/<short-hostname>.json`.
- `trustedWorkspaces` and list-valued `permissions.*` (`allow`, `ask`, `deny`) are **additively concatenated and deduplicated**; dict keys shallow-merge; scalar keys overwrite.
- Removing an entry from `settings.base.json` or `machines/<host>.json` will **not** delete it from an already-populated `~/.gemini/antigravity-cli/settings.json`—clean it up in both places when revoking a permission or workspace.
- Never place machine-local filesystem paths (such as `trustedWorkspaces`) in `cli/settings.base.json`; put them in `cli/machines/$(hostname -s).json`.

## Component Conventions & Safety Invariants

- **Dependencies & Portability:** Stick strictly to POSIX `sh`/`bash`, `jq`, and the Python 3.11+ standard library (uses `tomllib`, no third-party `pip` packages) so new machines bootstrap cleanly. Invoke Python scripts with `python3 -B` (and set `sys.dont_write_bytecode = True` in entry scripts) to avoid writing `__pycache__/` into the live repo. Keep `cli/statusline.sh`, `tools/agy-sessions/agy-sessions`, and `plugins/personal-kit/update_sessions.py` executable (`chmod +x`).
- **Native Plugin Layout (`plugins/<name>/`):** Plugins registered via `plugins.json` do not run extension staging—`plugin.json` and `hooks.json` **must** stay directly at `plugins/<name>/` root (never nested under `hooks/hooks.json`), with consolidated rules in `plugins/<name>/rules/AGENTS.md`.
- **Lifecycle Hooks (`hooks.json` & `update_sessions.py`):**
  - Hooks execute synchronously and block the agent loop across **all** projects (`timeout: 5` seconds, working directory set to `plugins/personal-kit/`). Keep execution fast and side-effect-safe.
  - Hook `stdin` payloads use **camelCase** keys (`conversationId`, `workspacePaths`, `transcriptPath`, `hookEventName`, `cwd`).
  - **Fail open:** Always catch unexpected exceptions, exit `0`, and print valid JSON to `stdout` (`{"decision": "allow"}` when `hookEventName == "Stop"`, `{}` otherwise) so hook errors never stall or break `agy`.
- **Session Management (`tools/agy-sessions/` & `update_sessions.py`):**
  - **Opt-in only:** Recognizes `.agy-sessions.toml` at the project root (preferred; created by `agy-sessions init`), with fallback to an existing `<project>/agy/` folder (walking up and stopping at `.git`, `$HOME`, or `/`).
  - Supports `[paths]` with `{host}` hostname expansion (`agy/{host}/sessions.md`, `agy/{host}/transcripts`, `agy/handoffs`), `[[group]]` classification (`ids`, `<key>: ...`, `names`), prompt counts, hand-off notes linking, zero-prompt session listing (`Without prompts`), and `Checks`.
  - `sessions.md` is fully auto-generated (not manually edited) and written atomically (`tempfile.mkstemp` + `os.replace`) only when the body changes (or on explicit `agy-sessions index`).
  - `agy-sessions dump` renders `transcript_full.jsonl` (falling back to `transcript.jsonl`) to Markdown; `agy-sessions delete` guards live sessions via `fcntl.flock` on `presence/<ID>.lock` before removing local session files and `conversation_summaries.db` rows.

## Verification (Sandboxed)

All validation checks can run inside the standard terminal sandbox (`BypassSandbox: false`):

```bash
# 1. Syntax & JSON validation
bash -n setup.sh && sh -n cli/statusline.sh && \
  python3 -B -c "import ast, glob; [ast.parse(open(p).read()) for p in ['plugins/personal-kit/update_sessions.py', 'tools/agy-sessions/agy-sessions'] + glob.glob('tools/agy-sessions/agy_sessions/*.py')]" && \
  jq empty cli/settings.base.json cli/machines/*.json plugins/personal-kit/plugin.json plugins/personal-kit/hooks.json

# 2. Smoke-test statusline.sh
printf '{"model":{"display_name":"Gemini"},"cycle_mode":"accept-edits","conversation_title":"Test","context_window":{"used_percentage":42}}' | ./cli/statusline.sh

# 3. Smoke-test setup.sh in an isolated temp directory
tmp_dir="$(mktemp -d)" && ./setup.sh --gemini-dir "$tmp_dir" && rm -rf "$tmp_dir"
```
