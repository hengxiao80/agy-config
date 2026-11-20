"""Delete Antigravity CLI sessions of a project by group or session ID prefix.

Groups are those defined in `.agy-sessions.toml`, plus `empty` (sessions without
prompts). Ungrouped sessions are work not yet filed, so they are deleted only by
ID prefix.

Per session, deletes what `agy` keeps under `~/.gemini/antigravity-cli/`:
  - `conversations/<ID>.db` (and `-shm`, `-wal`)
  - `brain/<ID>/` (transcripts, artifacts, scratch, tasks)
  - `annotations/<ID>.pbtxt`
  - `presence/<ID>.lock`
and removes the session's rows from `conversation_summaries.db`.

Leaves alone `~/.gemini/antigravity-cli/history.jsonl` and any Markdown dumps in
the project's transcripts directory. Skips sessions currently running (checked
via non-blocking `flock` on `presence/<ID>.lock` and running DB status).
"""

import fcntl
import os
import shutil
import sqlite3
import sys

from . import index
from .config import tilde


def is_session_live(app_data_dir: str, s: dict) -> bool:
    """Return True if a running `agy` process currently holds this session open."""
    sid = s["id"]
    lock_path = os.path.join(app_data_dir, "presence", f"{sid}.lock")
    if os.path.isfile(lock_path):
        fd = None
        try:
            fd = os.open(lock_path, os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
            except BlockingIOError:
                return True
        except OSError:
            pass
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    shm_path = os.path.join(app_data_dir, "conversations", f"{sid}.db-shm")
    if os.path.exists(shm_path) and (
        s.get("status") == "CASCADE_RUN_STATUS_RUNNING" or s.get("not_fully_idle")
    ):
        return True
    return False


def leftovers(app_data_dir: str, s: dict) -> list[str]:
    sid = s["id"]
    base_dir = app_data_dir
    if s.get("row_app_dir"):
        candidate_base = os.path.join(os.path.dirname(app_data_dir), s["row_app_dir"])
        if os.path.isdir(candidate_base):
            base_dir = candidate_base

    paths = [
        os.path.join(base_dir, "conversations", f"{sid}.db"),
        os.path.join(base_dir, "conversations", f"{sid}.db-shm"),
        os.path.join(base_dir, "conversations", f"{sid}.db-wal"),
        os.path.join(base_dir, "brain", sid),
        os.path.join(base_dir, "annotations", f"{sid}.pbtxt"),
        os.path.join(base_dir, "presence", f"{sid}.lock"),
    ]
    return [p for p in paths if os.path.lexists(p)]


def fmt_size(path: str) -> str:
    if os.path.isdir(path) and not os.path.islink(path):
        total = 0
        for d, _, fs in os.walk(path):
            for f in fs:
                try:
                    total += os.lstat(os.path.join(d, f)).st_size
                except OSError:
                    pass
    else:
        total = os.lstat(path).st_size
    if total >= 1_000_000:
        return f"{total / 1e6:.1f} MB"
    return f"{total / 1e3:.1f} kB"


def select(groups: list[str], ids: list[str], sessions: list[dict], project_groups: list[dict]) -> list[dict]:
    by_group, empty, _ = index.classify(sessions, project_groups)
    keys = [g["key"] for g in project_groups]
    chosen = {}
    for key in groups:
        if key in keys:
            ss = by_group.get(key, [])
        elif key == "empty":
            ss = empty
        elif key == "ungrouped":
            sys.exit("Ungrouped sessions are deleted only by ID.")
        else:
            sys.exit(f"No group {key!r}; groups: {', '.join(keys + ['empty'])}")
        if not ss:
            print(f"Group {key!r} has no sessions.")
        for s in ss:
            chosen[s["id"]] = s

    for prefix in ids:
        matches = [s for s in sessions if s["id"].lower().startswith(prefix.lower())]
        if len(matches) != 1:
            detail = ", ".join(s["id"] for s in matches) if matches else "no session of this project"
            sys.exit(f"{prefix!r} matches {detail}")
        chosen[matches[0]["id"]] = matches[0]

    return sorted(chosen.values(), key=lambda s: (s["t0"], s["id"]))


def purge_from_summaries_db(app_data_dir: str, sids: list[str]) -> None:
    db_path = os.path.join(app_data_dir, "conversation_summaries.db")
    if not sids or not os.path.isfile(db_path):
        return
    con = None
    try:
        con = sqlite3.connect(db_path, timeout=2.0)
        cur = con.cursor()
        tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for sid in sids:
            if "conversation_summaries" in tables:
                cur.execute("DELETE FROM conversation_summaries WHERE conversation_id = ?", (sid,))
            if "conversations_fts" in tables:
                cur.execute("DELETE FROM conversations_fts WHERE conversation_id = ?", (sid,))
            if "conversation_parents" in tables:
                cur.execute(
                    "DELETE FROM conversation_parents WHERE conversation_id = ? OR parent_id = ?",
                    (sid, sid),
                )
        con.commit()
    except sqlite3.Error as e:
        print(
            f"warning: could not update conversation_summaries.db ({e}); "
            "agy will reconcile it on next startup.",
            file=sys.stderr,
        )
    finally:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass


def run(p, ids: list[str], groups: list[str], dry_run: bool = False, yes: bool = False) -> int:
    sessions = index.load_sessions(p)
    chosen = select(groups, ids, sessions, p.groups)

    named = {}
    for h in index.read_handoffs(p):
        for n in {h["by"], h["opens"]} - {None}:
            named.setdefault(index.norm(n), []).append(os.path.basename(h["path"]))

    todo = []
    for s in chosen:
        sid = s["id"]
        noun = "prompt" if s["prompts"] == 1 else "prompts"
        print(f"{sid}  {s['t0']:%Y-%m-%d}  {s['prompts']} {noun}  {index.label(s)}")
        if is_session_live(p.app_data_dir, s):
            print("    skipped: still running")
            continue
        paths = leftovers(p.app_data_dir, s)
        for f in paths:
            print(f"    {fmt_size(f):>10}  {tilde(f)}")
        for d in index.dumps(p, s):
            print(f"    kept: {os.path.relpath(d, p.root)}")
        if s.get("title") and index.norm(s["title"]) in named:
            print(f"    named in hand-off note(s): {', '.join(named[index.norm(s['title'])])}")
        todo.append((s, paths))

    if not todo or dry_run:
        suffix = "; dry run, nothing deleted." if dry_run else "."
        print(f"{len(todo)} session(s) to delete{suffix}")
        return 0

    if not yes:
        if not sys.stdin.isatty():
            sys.exit("Not a terminal, so no one to ask: pass -y to delete, or -n to only list.")
        ans = input(f"Delete these {len(todo)} session(s)? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            sys.exit("Nothing deleted.")

    deleted_ids = []
    for s, paths in todo:
        for f in paths:
            if os.path.isdir(f) and not os.path.islink(f):
                shutil.rmtree(f)
            elif os.path.lexists(f):
                os.remove(f)
        deleted_ids.append(s["id"])
        print(f"deleted {s['id']}")

    purge_from_summaries_db(p.app_data_dir, deleted_ids)
    summary, _ = index.write(p, force=True)
    print(summary)
    return 0
