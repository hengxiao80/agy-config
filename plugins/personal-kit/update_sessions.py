#!/usr/bin/env python3
"""Global Antigravity hook to maintain <project>/agy/<hostname>/sessions.md.

Opt-in per project: only updates sessions.md when <project>/agy/ already exists.
"""

from datetime import datetime
import json
import os
import re
import socket
import sys
import tempfile

ROW_RE = re.compile(
    r"^\|\s*\d+\s*"
    r"\|\s*([^|]*?)\s*"
    r"\|\s*([^|]*?)\s*"
    r"\|\s*`?([^|`]*?)`?\s*"
    r"\|\s*`?([0-9a-fA-F-]{8,})`?\s*"
    r"\|\s*([^|]*?)\s*\|$"
)


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


def parse_iso_local(ts: str):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def read_transcript_bounds(transcript_path: str):
    if not transcript_path or not os.path.isfile(transcript_path):
        return None, None
    first_ts = None
    last_ts = None
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = obj.get("created_at")
                if ts:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts
    except OSError:
        return None, None
    return parse_iso_local(first_ts), parse_iso_local(last_ts)


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


def render_markdown(project_root: str, host: str, tz_label: str, today_str: str, sessions: list) -> str:
    project_name = os.path.basename(project_root)
    lines = [
        f"# Antigravity CLI Sessions — `{project_name}`",
        "",
        f"- **Workspace:** `{project_root}`",
        f"- **Machine:** `{host}`",
        f"- **Last updated:** {today_str}",
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


def update_index(payload: dict) -> None:
    conv_id = (payload.get("conversationId") or "").strip()
    if not conv_id:
        return

    project_root = find_opted_in_project(payload)
    if not project_root:
        return

    hook_event = payload.get("hookEventName") or ""
    transcript_path = payload.get("transcriptPath") or ""

    host = (socket.gethostname().split(".")[0] or "default").strip()
    host_dir = os.path.join(project_root, "agy", host)
    sessions_path = os.path.join(host_dir, "sessions.md")

    sessions, by_id = load_existing_sessions(sessions_path)
    first_dt, last_dt = read_transcript_bounds(transcript_path)

    # Skip zero-turn empty sessions at SessionStart (e.g., opening agy just to run /resume).
    if hook_event == "SessionStart" and first_dt is None and conv_id not in by_id:
        return

    now_local = datetime.now().astimezone()
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

    tz_label = now_local.tzname() or "Local"
    today_str = now_local.strftime("%Y-%m-%d")
    new_content = render_markdown(project_root, host, tz_label, today_str, sessions)

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


def main() -> int:
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
