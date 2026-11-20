#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEMINI_DIR="${HOME}/.gemini"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gemini-dir)
      GEMINI_DIR="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [--gemini-dir <path>]"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

CLI_DIR="${GEMINI_DIR}/antigravity-cli"
CFG_DIR="${GEMINI_DIR}/config"
HOST_NAME="$(hostname -s 2>/dev/null || hostname | cut -d. -f1)"

mkdir -p "${CLI_DIR}" "${CFG_DIR}"
chmod +x "${REPO_DIR}/cli/statusline.sh" "${REPO_DIR}/plugins/personal-kit/update_sessions.py"

# 1. Symlink statusline.sh (safe to symlink: agy executes it without rewriting it)
ln -sfn "${REPO_DIR}/cli/statusline.sh" "${CLI_DIR}/statusline.sh"

# 2. Merge settings.base.json + optional machines/<HOST>.json into settings.json
#    (written as a regular file because agy atomically replaces settings.json on /config saves)
python3 -B - "${REPO_DIR}" "${CLI_DIR}/settings.json" "${CFG_DIR}/plugins.json" "${HOST_NAME}" <<'PY'
import json
import os
import sys

repo_dir, settings_path, plugins_json_path, host_name = sys.argv[1:5]


def load_json(path):
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        return json.loads(content) if content else {}


def dedupe(seq):
    seen = set()
    out = []
    for item in seq:
        key = json.dumps(item, sort_keys=True) if isinstance(item, (dict, list)) else item
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def merge_settings(base, overlay):
    merged = dict(base)
    for k, v in overlay.items():
        if k == "trustedWorkspaces" and isinstance(v, list):
            prev = merged.get("trustedWorkspaces")
            merged["trustedWorkspaces"] = dedupe((prev if isinstance(prev, list) else []) + v)
        elif k == "permissions" and isinstance(v, dict):
            prev_perm = merged.get("permissions")
            perm = dict(prev_perm) if isinstance(prev_perm, dict) else {}
            for pk, pv in v.items():
                if isinstance(pv, list):
                    old_list = perm.get(pk)
                    perm[pk] = dedupe((old_list if isinstance(old_list, list) else []) + pv)
                else:
                    perm[pk] = pv
            merged["permissions"] = perm
        elif isinstance(v, dict) and isinstance(merged.get(k), dict):
            sub = dict(merged[k])
            sub.update(v)
            merged[k] = sub
        else:
            merged[k] = v
    return merged


existing = load_json(settings_path)
base_cfg = load_json(os.path.join(repo_dir, "cli", "settings.base.json"))
host_cfg_path = os.path.join(repo_dir, "cli", "machines", f"{host_name}.json")
host_cfg = load_json(host_cfg_path)

final_settings = merge_settings(existing, base_cfg)
if host_cfg:
    final_settings = merge_settings(final_settings, host_cfg)

with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(final_settings, f, indent=2)
    f.write("\n")

# 3. Register repo's plugins/ directory in ~/.gemini/config/plugins.json
home = os.path.abspath(os.path.expanduser("~"))
plugins_dir_abs = os.path.abspath(os.path.join(repo_dir, "plugins"))
if plugins_dir_abs.startswith(home + os.sep):
    plugins_dir_ref = "~/" + os.path.relpath(plugins_dir_abs, home)
else:
    plugins_dir_ref = plugins_dir_abs

plugins_cfg = load_json(plugins_json_path)
entries = plugins_cfg.get("entries")
if not isinstance(entries, list):
    entries = []

already = any(
    isinstance(e, dict) and e.get("path") in (plugins_dir_ref, plugins_dir_abs)
    for e in entries
)
if not already:
    entries.append({"path": plugins_dir_ref})
plugins_cfg["entries"] = entries

with open(plugins_json_path, "w", encoding="utf-8") as f:
    json.dump(plugins_cfg, f, indent=2)
    f.write("\n")
PY

echo "Configured Antigravity for host '${HOST_NAME}':"
echo "  - Linked status line : ${CLI_DIR}/statusline.sh -> ${REPO_DIR}/cli/statusline.sh"
echo "  - Merged CLI settings: ${CLI_DIR}/settings.json"
if [[ -f "${REPO_DIR}/cli/machines/${HOST_NAME}.json" ]]; then
  echo "    (included host overrides from cli/machines/${HOST_NAME}.json)"
fi
echo "  - Registered plugins : ${CFG_DIR}/plugins.json -> ${REPO_DIR}/plugins"
