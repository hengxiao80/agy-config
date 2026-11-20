#!/usr/bin/env python3
"""Global Antigravity hook shim invoking tools/agy-sessions.

Always fails open in hook mode so hook errors never stall or break `agy`.
Also supports `--sync [project_dir] [--app-data-dir <dir>]` as an alias for
`agy-sessions index`.
"""

import os
import sys

sys.dont_write_bytecode = True

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", ".."))
_TOOLS_DIR = os.path.join(_REPO_ROOT, "tools", "agy-sessions")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--sync":
        from agy_sessions.cli import main as cli_main

        return cli_main(["index"] + sys.argv[2:])

    try:
        from agy_sessions.cli import run_hook_stdin

        return run_hook_stdin()
    except Exception:
        # Fail open even if importing agy_sessions fails (e.g., Python < 3.11).
        sys.stdout.write('{"decision": "allow"}\n')
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
