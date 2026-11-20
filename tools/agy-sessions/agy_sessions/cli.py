"""agy-sessions: index, dump, and delete a project's Antigravity CLI sessions.

A project is the nearest directory at or above the current one with
`.agy-sessions.toml` (`agy-sessions init` writes one), or a legacy `agy/` dir.
"""

import argparse
import json
import os
import subprocess
import sys

from . import delete, dump, index
from .config import (
    CONFIG,
    SKELETON,
    ConfigError,
    Project,
    add_gitignore,
    derive_app_data_dir,
    find_toml_root,
    gitignore_pattern,
)


def _project(start: str = ".", app_data_dir: str = "") -> Project:
    p = Project.find(start, app_data_dir=app_data_dir, allow_legacy=True)
    if not p:
        sys.exit(f"No {CONFIG} (or agy/ directory) here or above; `agy-sessions init` writes one.")
    return p


def _in_git(d: str) -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", d, "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
        )
        return r.stdout.strip() == "true"
    except OSError:
        return False


def cmd_init(args) -> int:
    """Write skeleton `.agy-sessions.toml`, create agy/ directories, build index, and update .gitignore."""
    root = os.path.realpath(args.dir)
    path = os.path.join(root, CONFIG)
    if not os.path.isdir(root):
        sys.exit(f"{root} is not a directory.")
    if not _in_git(root):
        sys.exit(f"{root} is not inside a git work tree; nothing done. Run `git init` first.")
    if os.path.exists(path):
        print(f"{path} exists; nothing done.")
        return 1

    outer = find_toml_root(os.path.dirname(root))
    with open(path, "x", encoding="utf-8") as f:
        f.write(SKELETON)
    print(f"wrote {path}")
    if outer:
        print(f"Sessions started here now belong to this project, not to the one at {outer}.")

    p = Project(root, app_data_dir=args.app_data_dir or "")
    for d in (os.path.dirname(p.index), p.handoffs, p.transcripts):
        if d and not os.path.isdir(d):
            os.makedirs(d)
            print(f"created {d}/")

    summary, found = index.write(p, force=True)
    print(summary)
    if found:
        print("Checks:\n" + "\n".join(f"  {c}" for c in found))

    ignore_entries = [gitignore_pattern(p.path_templates["index"], is_dir=False)]
    if p.path_templates.get("transcripts"):
        ignore_entries.append(gitignore_pattern(p.path_templates["transcripts"], is_dir=True))
    print(add_gitignore(root, ignore_entries))
    return 0


def cmd_index(args) -> int:
    p = _project(args.dir, app_data_dir=args.app_data_dir or "")
    if args.stdout:
        print(index.build(p)[0])
        return 0
    summary, found = index.write(p, force=True)
    print(summary)
    if found:
        print("Checks:\n" + "\n".join(f"  {c}" for c in found))
    return 0


def cmd_delete(args) -> int:
    if not args.ids and not args.group:
        sys.exit("Give session IDs or --group.")
    p = _project(args.dir, app_data_dir=args.app_data_dir or "")
    return delete.run(p, args.ids, args.group, dry_run=args.dry_run, yes=args.yes)


def cmd_dump(args) -> int:
    p = _project(args.dir, app_data_dir=args.app_data_dir or "")
    if not p.transcripts:
        sys.exit(f'{p.config}: transcripts are off ([paths] transcripts = "")')

    target = args.session
    title = ""
    if os.path.isfile(target):
        tpath = os.path.abspath(target)
        # Try to recover conversation UUID from .../brain/<id>/.system_generated/logs/transcript*.jsonl
        parts = tpath.split(os.sep)
        sid = os.path.splitext(os.path.basename(tpath))[0]
        if "brain" in parts:
            idx = parts.index("brain")
            if idx + 1 < len(parts):
                sid = parts[idx + 1]
    else:
        sessions = index.load_sessions(p)
        matches = [s for s in sessions if s["id"].lower().startswith(target.lower())]
        if len(matches) != 1:
            detail = ", ".join(s["id"] for s in matches) if matches else "no session of this project"
            sys.exit(f"{target!r} is not a file and matches {detail}")
        s = matches[0]
        sid = s["id"]
        title = s.get("title") or ""
        full_path = index.transcript_path_for(
            p.app_data_dir, sid, row_app_dir=s.get("row_app_dir", ""), full=True
        )
        tpath = full_path if os.path.isfile(full_path) else s["transcript"]
        if not os.path.isfile(tpath):
            sys.exit(f"No transcript file found for session {sid} (empty session?)")

    out = dump.convert(tpath, sid, args.label, p.transcripts, title=title)
    print(out)
    # Refresh index so the new dump appears in the Transcript dumps column.
    index.write(p, force=True)
    return 0


def run_hook_stdin() -> int:
    """Antigravity lifecycle hook entrypoint (fail-open; camelCase stdin, JSON stdout)."""
    raw = sys.stdin.read()
    payload = {}
    if raw.strip():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}

    hook_event = payload.get("hookEventName") or ""
    try:
        candidates = []
        if payload.get("cwd"):
            candidates.append(payload["cwd"])
        for wp in payload.get("workspacePaths") or []:
            if wp and wp not in candidates:
                candidates.append(wp)

        tpath = payload.get("transcriptPath") or ""
        app_data_dir = derive_app_data_dir(tpath)

        p = None
        for start in candidates:
            p = Project.find(start, app_data_dir=app_data_dir, allow_legacy=True)
            if p:
                break

        if p is not None:
            conv_id = (payload.get("conversationId") or "").strip()
            active = (
                {"id": conv_id, "transcript_path": tpath, "event": hook_event}
                if conv_id
                else None
            )
            index.write(p, active=active, force=False)
    except Exception:
        # Fail open: never block or crash the Antigravity agent loop.
        pass

    if hook_event == "Stop":
        sys.stdout.write('{"decision": "allow"}\n')
    else:
        sys.stdout.write("{}\n")
    return 0


def cmd_hook(args) -> int:
    return run_hook_stdin()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="agy-sessions",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")

    a = sub.add_parser(
        "init",
        help=f"start a project: {CONFIG}, agy/ dirs, index, .gitignore lines",
        description=cmd_init.__doc__,
    )
    a.add_argument("dir", nargs="?", default=".", metavar="DIR")
    a.add_argument("--app-data-dir", default="", help=argparse.SUPPRESS)
    a.set_defaults(run=cmd_init)

    a = sub.add_parser("index", help="rebuild the session index")
    a.add_argument("dir", nargs="?", default=".", metavar="DIR")
    a.add_argument("--stdout", action="store_true", help="print the index instead of writing it")
    a.add_argument("--app-data-dir", default="", help=argparse.SUPPRESS)
    a.set_defaults(run=cmd_index)

    a = sub.add_parser(
        "delete",
        help="delete sessions by group or ID",
        description=delete.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    a.add_argument("ids", nargs="*", help="session IDs, or unique prefixes of them")
    a.add_argument(
        "-g",
        "--group",
        action="append",
        default=[],
        help=f"a group key of {CONFIG}, or empty (repeatable)",
    )
    a.add_argument("-n", "--dry-run", action="store_true", help="only list what would be deleted")
    a.add_argument("-y", "--yes", action="store_true", help="delete without asking")
    a.add_argument("--dir", default=".", help=argparse.SUPPRESS)
    a.add_argument("--app-data-dir", default="", help=argparse.SUPPRESS)
    a.set_defaults(run=cmd_delete)

    a = sub.add_parser(
        "dump",
        help="dump a session transcript to Markdown",
        description=dump.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    a.add_argument("session", help="a transcript .jsonl, or a session ID or unique prefix")
    a.add_argument("label", nargs="?", default="manual", help="end of the dump filename (default: manual)")
    a.add_argument("--dir", default=".", help=argparse.SUPPRESS)
    a.add_argument("--app-data-dir", default="", help=argparse.SUPPRESS)
    a.set_defaults(run=cmd_dump)

    a = sub.add_parser("hook", help="run from Antigravity lifecycle hooks (JSON on stdin)")
    a.set_defaults(run=cmd_hook)

    args = ap.parse_args(argv)
    try:
        return args.run(args)
    except ConfigError as e:
        print(f"agy-sessions: {e}", file=sys.stderr)
        return 1
