#!/usr/bin/env python3
"""Global Antigravity hook to maintain <project>/agy/<hostname>/sessions.md.

Opt-in per project: only updates sessions.md when <project>/agy/ already exists.
Syncs all non-empty local sessions for the project (including session titles)
from Antigravity's conversation_summaries.db and transcript logs.
"""

from datetime import datetime
import json
import os
import re
import socket
import sqlite3
import sys
import tempfile
from urllib.parse import unquote, urlparse

ROW_RE = re.compile(
    r"^\|\s*\d+\s*"
    r"\|\s*([^|]*?)\s*"
    r"\|\s*([^|]*?)\s*"
    r"\|\s*`?([^|`]*?)`?\s*"
    r"\|\s*`?([0-9a-fA-F-]{8,})`?\s*"
    r"\|\s*([^|]*?)\s*\|$"
)
FRAC_SEC_RE = re.compile(r"\.(\d+)")
TAIL_READ_BYTES = 131072  # 128 KiB tail scan for fast O(1) last-timestamp lookup


def find_opted_in_project(payload: dict):
    """Return project root if it contains an agy/ directory, else None."""
    home = os.path.abspath(os.path.expanduser("~"))
    candidates = []
    cwd = payload.get("cwd")
    if cwd:
        candidates.append(cwd)
    for wp in payload.get("workspacePaths") or []:
        if wp and wp not in candidates:
            candidates.append(wp)

    for start in candidates:
        cur = os.path.abspath(start)
        while True:
            if cur in ("/", home):
                break
            if os.path.isdir(os.path.join(cur, "agy")):
                return cur
            if os.path.exists(os.path.join(cur, ".git")):
                break
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    return None


def derive_app_data_dir(transcript_path: str) -> str:
    """Derive ~/.gemini/antigravity-cli (or sibling app dir) from transcriptPath."""
    if transcript_path:
        cur = os.path.abspath(transcript_path)
        while True:
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            if os.path.basename(cur) == "brain":
                return parent
            cur = parent
    return os.path.abspath(os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli"))


def parse_file_uri(uri: str):
    """Decode a file:// URI (or raw path) into an absolute local path."""
    if not uri or not isinstance(uri, str):
        return None
    if uri.startswith("file://"):
        parsed = urlparse(uri)
        path = unquote(parsed.path)
        return os.path.abspath(path) if path else None
    if os.path.isabs(uri):
        return os.path.abspath(uri)
    return None


def parse_iso_local(ts: str):
    """Parse an ISO-8601 / Go SQLite timestamp into local timezone datetime."""
    if not ts or not isinstance(ts, str):
        return None
    s = ts.strip()
    if not s or s.startswith("0001-01-01"):
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Normalize variable-length fractional seconds (e.g. 5 digits from Go) to 6 digits for Python < 3.11.
    s = FRAC_SEC_RE.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), s, count=1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.year <= 1:
        return None
    return dt.astimezone() if dt.tzinfo is not None else dt.astimezone()


def _extract_created_at(raw_line: bytes):
    line = raw_line.strip()
    if not line or b"created_at" not in line:
        return None
    try:
        obj = json.loads(line.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    return parse_iso_local(obj.get("created_at"))


def read_transcript_bounds(transcript_path: str):
    """Return (first_dt, last_dt) from transcript.jsonl in O(1) time for large files."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return None, None
    first_dt = None
    last_dt = None
    try:
        file_size = os.path.getsize(transcript_path)
        if file_size == 0:
            return None, None
        with open(transcript_path, "rb") as f:
            # 1. Scan forward for the first timestamped entry (usually line 1).
            for raw_line in f:
                dt = _extract_created_at(raw_line)
                if dt is not None:
                    first_dt = dt
                    break

            if first_dt is None:
                return None, None

            # 2. Scan backward from the tail of the file for the last timestamped entry.
            if file_size > TAIL_READ_BYTES:
                f.seek(file_size - TAIL_READ_BYTES)
                # Drop partial leading line after seeking into the middle of the file.
                f.readline()
            else:
                f.seek(0)
            tail_lines = f.read().splitlines()
            for raw_line in reversed(tail_lines):
                dt = _extract_created_at(raw_line)
                if dt is not None:
                    last_dt = dt
                    break

            # Fallback full scan only if the final entry exceeded TAIL_READ_BYTES.
            if last_dt is None and file_size > TAIL_READ_BYTES:
                f.seek(0)
                for raw_line in f:
                    dt = _extract_created_at(raw_line)
                    if dt is not None:
                        last_dt = dt
    except OSError:
        return None, None
    return first_dt, last_dt or first_dt


def sanitize_title(title: str) -> str:
    if not title:
        return ""
    cleaned = re.sub(r"\s+", " ", str(title)).strip()
    return cleaned.replace("|", "\\|")


def format_date_and_time(start_dt: datetime, end_dt: datetime):
    date_str = start_dt.strftime("%Y-%m-%d")
    start_hm = start_dt.strftime("%H:%M")
    if end_dt.date() == start_dt.date():
        end_hm = end_dt.strftime("%H:%M")
        time_str = f"{start_hm}–{end_hm}"
    else:
        time_str = f"{start_hm} – {end_dt.strftime('%m-%d %H:%M')}"
    return date_str, time_str


def load_existing_sessions(sessions_path: str):
    """Fallback loader used only if conversation_summaries.db is unavailable."""
    sessions = []
    by_id = {}
    if not os.path.isfile(sessions_path):
        return sessions, by_id
    with open(sessions_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            m = ROW_RE.match(raw_line.strip())
            if not m:
                continue
            date_str, time_str, short_id, full_id, name = m.groups()
            entry = {
                "date": date_str.strip(),
                "time": time_str.strip(),
                "short_id": short_id.strip() or full_id[:8],
                "full_id": full_id.strip(),
                "name": name.strip(),
            }
            by_id[entry["full_id"]] = entry
            sessions.append(entry)
    return sessions, by_id


def _transcript_path_for(app_data_dir: str, row_app_dir: str, conv_id: str) -> str:
    candidates = []
    if row_app_dir:
        candidates.append(
            os.path.join(
                os.path.dirname(app_data_dir),
                row_app_dir,
                "brain",
                conv_id,
                ".system_generated",
                "logs",
                "transcript.jsonl",
            )
        )
    candidates.append(
        os.path.join(
            app_data_dir,
            "brain",
            conv_id,
            ".system_generated",
            "logs",
            "transcript.jsonl",
        )
    )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[0]


def _workspace_matches_project(workspace_uris_raw: str, project_root: str, project_real: str) -> bool:
    if not workspace_uris_raw:
        return False
    try:
        uris = json.loads(workspace_uris_raw)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(uris, list):
        return False

    for uri in uris:
        path = parse_file_uri(uri)
        if not path:
            continue
        owner = find_opted_in_project({"workspacePaths": [path]})
        if owner and (owner == project_root or os.path.realpath(owner) == project_real):
            return True
    return False


def load_db_sessions(app_data_dir: str, project_root: str):
    """Load all top-level sessions for project_root from conversation_summaries.db.

    Returns a dict keyed by conversation_id, or None if the DB cannot be read.
    """
    db_path = os.path.join(app_data_dir, "conversation_summaries.db")
    if not os.path.isfile(db_path):
        return None

    project_real = os.path.realpath(project_root)
    results = {}
    con = None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        rows = cur.execute("SELECT * FROM conversation_summaries").fetchall()
    except sqlite3.Error:
        return None
    finally:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass

    for row in rows:
        keys = set(row.keys())
        cid = (row["conversation_id"] or "").strip() if "conversation_id" in keys else ""
        if not cid:
            continue

        # Skip subagent/nested child conversations; only index top-level user sessions.
        parent_id = (row["parent_conversation_id"] or "").strip() if "parent_conversation_id" in keys else ""
        nesting = (row["nesting_depth"] or 0) if "nesting_depth" in keys else 0
        if parent_id or nesting > 0:
            continue

        ws_raw = row["workspace_uris"] if "workspace_uris" in keys else ""
        if not _workspace_matches_project(ws_raw, project_root, project_real):
            continue

        step_count = (row["step_count"] or 0) if "step_count" in keys else 0
        title = sanitize_title(row["title"] if "title" in keys else "")
        row_app_dir = (row["app_data_dir"] or "").strip() if "app_data_dir" in keys else ""
        db_last_dt = parse_iso_local(row["last_modified_time"] if "last_modified_time" in keys else "")

        tpath = _transcript_path_for(app_data_dir, row_app_dir, cid)
        first_dt, last_dt = read_transcript_bounds(tpath)

        # Skip zero-turn empty conversations that never had a prompt/transcript.
        if step_count <= 0 and first_dt is None and db_last_dt is None:
            continue

        start_dt = first_dt or db_last_dt
        end_dt = last_dt or db_last_dt or first_dt
        if start_dt and end_dt and end_dt < start_dt:
            end_dt = start_dt
        if start_dt is None:
            continue

        results[cid] = {
            "start_dt": start_dt,
            "end_dt": end_dt,
            "short_id": cid[:8],
            "full_id": cid,
            "name": title,
            "step_count": step_count,
        }

    return results


def render_markdown(project_root: str, host: str, tz_label: str, today_str: str, sessions: list) -> str:
    project_name = os.path.basename(project_root)
    lines = [
        f"# Antigravity CLI Sessions — `{project_name}`",
        "",
        f"- **Workspace:** `{project_root}`",
        f"- **Machine:** `{host}`",
        f"- **Last updated:** {today_str}",
        "- **Note:** Auto-generated from Antigravity session history; do not edit manually.",
        "",
        "> **Resuming a session:**",
        "> - Inside `agy`: `/resume <short-id>` (or run `/resume` to browse interactively)",
        "> - From shell: `agy --conversation <session-id>` (or `agy -c` for the most recent session)",
        "",
        f"| # | Date | Time ({tz_label}) | Short ID | Full Session ID | Name |",
        "| :- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for idx, s in enumerate(sessions, start=1):
        name_cell = f" {s['name']} " if s["name"] else " "
        lines.append(
            f"| {idx} | {s['date']} | {s['time']} | `{s['short_id']}` | `{s['full_id']}` |{name_cell}|"
        )
    lines.append("")
    return "\n".join(lines)


def write_atomic_if_changed(sessions_path: str, host_dir: str, new_content: str) -> None:
    old_content = None
    if os.path.isfile(sessions_path):
        with open(sessions_path, "r", encoding="utf-8") as f:
            old_content = f.read()

    if new_content == old_content:
        return

    os.makedirs(host_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".sessions.", suffix=".md", dir=host_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp_path, sessions_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def update_index(payload: dict, app_data_dir_override: str = "") -> None:
    project_root = find_opted_in_project(payload)
    if not project_root:
        return

    conv_id = (payload.get("conversationId") or "").strip()
    hook_event = payload.get("hookEventName") or ""
    transcript_path = payload.get("transcriptPath") or ""
    app_data_dir = (
        os.path.abspath(app_data_dir_override)
        if app_data_dir_override
        else derive_app_data_dir(transcript_path)
    )

    host = (socket.gethostname().split(".")[0] or "default").strip()
    host_dir = os.path.join(project_root, "agy", host)
    sessions_path = os.path.join(host_dir, "sessions.md")

    now_local = datetime.now().astimezone()
    tz_label = now_local.tzname() or "Local"
    today_str = now_local.strftime("%Y-%m-%d")

    db_sessions = load_db_sessions(app_data_dir, project_root)

    if db_sessions is not None:
        # Ensure the active conversation is included on non-empty turns even if
        # conversation_summaries.db hasn't flushed its latest step/timestamp yet.
        if conv_id:
            first_dt, last_dt = read_transcript_bounds(transcript_path)
            is_empty_start = hook_event == "SessionStart" and first_dt is None and conv_id not in db_sessions
            if not is_empty_start:
                existing = db_sessions.get(conv_id)
                start_dt = first_dt or (existing["start_dt"] if existing else now_local)
                candidates = [dt for dt in (last_dt, existing["end_dt"] if existing else None) if dt is not None]
                if hook_event != "SessionStart":
                    candidates.append(now_local)
                end_dt = max(candidates) if candidates else start_dt
                db_sessions[conv_id] = {
                    "start_dt": start_dt,
                    "end_dt": end_dt,
                    "short_id": conv_id[:8],
                    "full_id": conv_id,
                    "name": existing["name"] if existing else "",
                    "step_count": existing["step_count"] if existing else 1,
                }

        ordered = sorted(db_sessions.values(), key=lambda s: (s["start_dt"], s["full_id"]))
        if not ordered and not os.path.isfile(sessions_path):
            return

        sessions = []
        for item in ordered:
            date_str, time_str = format_date_and_time(item["start_dt"], item["end_dt"])
            sessions.append(
                {
                    "date": date_str,
                    "time": time_str,
                    "short_id": item["short_id"],
                    "full_id": item["full_id"],
                    "name": item["name"],
                }
            )
    else:
        # Fallback if conversation_summaries.db is missing or locked: update current session only.
        if not conv_id:
            return
        sessions, by_id = load_existing_sessions(sessions_path)
        first_dt, last_dt = read_transcript_bounds(transcript_path)
        if hook_event == "SessionStart" and first_dt is None and conv_id not in by_id:
            return

        start_dt = first_dt or now_local
        end_dt = max(last_dt, now_local) if last_dt else now_local
        date_str, time_str = format_date_and_time(start_dt, end_dt)

        if conv_id in by_id:
            entry = by_id[conv_id]
            if first_dt is not None or not entry["date"]:
                entry["date"] = date_str
            if first_dt is not None:
                entry["time"] = time_str
            elif "–" in entry["time"]:
                start_part = entry["time"].split("–", 1)[0].strip()
                entry["time"] = f"{start_part}–{now_local.strftime('%H:%M')}"
        else:
            entry = {
                "date": date_str,
                "time": time_str,
                "short_id": conv_id[:8],
                "full_id": conv_id,
                "name": "",
            }
            sessions.append(entry)
            by_id[conv_id] = entry

    new_content = render_markdown(project_root, host, tz_label, today_str, sessions)
    write_atomic_if_changed(sessions_path, host_dir, new_content)


def main() -> int:
    # Optional CLI mode: python3 -B update_sessions.py --sync [project_dir] [--app-data-dir <dir>]
    if len(sys.argv) > 1 and sys.argv[1] == "--sync":
        project_dir = os.getcwd()
        app_data_dir = ""
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--app-data-dir" and i + 1 < len(args):
                app_data_dir = args[i + 1]
                i += 2
            else:
                project_dir = args[i]
                i += 1
        update_index({"cwd": project_dir, "workspacePaths": [project_dir]}, app_data_dir_override=app_data_dir)
        return 0

    raw = sys.stdin.read()
    payload = {}
    if raw.strip():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}

    hook_event = payload.get("hookEventName") or ""
    try:
        update_index(payload)
    except Exception:
        # Never block or fail the agent loop if updating sessions.md hits an error.
        pass

    if hook_event == "Stop":
        sys.stdout.write('{"decision": "allow"}\n')
    else:
        sys.stdout.write("{}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
