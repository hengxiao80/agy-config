"""The session index: a project's Antigravity CLI sessions by group.

Reads Antigravity's `conversation_summaries.db` (read-only) and per-session
`transcript.jsonl` files, classifies sessions into the groups of
`.agy-sessions.toml`, counts user prompts, links Markdown transcript dumps and
hand-off notes, lists zero-prompt sessions separately, and reports config checks.
"""

from datetime import datetime
import glob
import json
import os
import re
import sqlite3
import tempfile
from urllib.parse import unquote, urlparse

from .config import CONFIG, find_root, tilde

KEYED = re.compile(r"\s*([A-Za-z0-9_-]+)\s*:")
FRAC_SEC_RE = re.compile(r"\.(\d+)")
USER_REQ_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def sanitize_cell(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip().replace("|", "\\|")


def parse_file_uri(uri: str):
    if not uri or not isinstance(uri, str):
        return None
    if uri.startswith("file://"):
        path = unquote(urlparse(uri).path)
        return os.path.abspath(path) if path else None
    if os.path.isabs(uri):
        return os.path.abspath(uri)
    return None


def parse_iso_local(ts: str):
    """Parse an ISO-8601 / Go SQLite timestamp into a local-timezone datetime."""
    if not ts or not isinstance(ts, str):
        return None
    s = ts.strip()
    if not s or s.startswith("0001-01-01"):
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = FRAC_SEC_RE.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), s, count=1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.year <= 1:
        return None
    return dt.astimezone()


def extract_user_request(content: str) -> str:
    """Extract the user's prompt text from an Antigravity USER_INPUT step."""
    if not content or not isinstance(content, str):
        return ""
    m = USER_REQ_RE.search(content)
    text = m.group(1) if m else content
    return text.strip()


def transcript_path_for(app_data_dir: str, conv_id: str, row_app_dir: str = "", full: bool = False) -> str:
    filename = "transcript_full.jsonl" if full else "transcript.jsonl"
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
                filename,
            )
        )
    candidates.append(
        os.path.join(
            app_data_dir,
            "brain",
            conv_id,
            ".system_generated",
            "logs",
            filename,
        )
    )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[-1]


def read_transcript_stats(path: str, handoff_ref=None) -> dict:
    """Scan a session's transcript.jsonl for timestamps, prompt count, first prompt, and handoff refs."""
    info = {
        "t0": None,
        "t1": None,
        "prompts": 0,
        "first_prompt": None,
        "refs": set(),
    }
    if not path or not os.path.isfile(path):
        return info

    first_ts = None
    last_ts = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                idx = line.find('"created_at":"')
                if idx >= 0:
                    end = line.find('"', idx + 14)
                    if end > idx + 14:
                        ts = line[idx + 14 : end]
                        if first_ts is None:
                            first_ts = ts
                        last_ts = ts
                if '"USER_INPUT"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") != "USER_INPUT":
                    continue
                src = d.get("source") or ""
                if src and src != "USER_EXPLICIT":
                    continue
                text = extract_user_request(d.get("content") or "")
                if not text:
                    continue
                info["prompts"] += 1
                if info["first_prompt"] is None:
                    info["first_prompt"] = " ".join(text.split())[:60]
                    if handoff_ref:
                        info["refs"].update(handoff_ref.findall(text))
    except OSError:
        return info

    info["t0"] = parse_iso_local(first_ts)
    info["t1"] = parse_iso_local(last_ts) or info["t0"]
    return info


def _workspace_belongs_to(workspace_uris_raw: str, project_root: str) -> bool:
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
        owner, _ = find_root(path, allow_legacy=True)
        if owner and os.path.realpath(owner) == project_root:
            return True
    return False


def _fallback_mtime(app_data_dir: str, conv_id: str, tpath: str) -> datetime:
    for candidate in (
        tpath,
        os.path.join(app_data_dir, "conversations", f"{conv_id}.db"),
        os.path.join(app_data_dir, "brain", conv_id),
    ):
        if candidate and os.path.exists(candidate):
            try:
                return datetime.fromtimestamp(os.path.getmtime(candidate)).astimezone()
            except OSError:
                pass
    return datetime.now().astimezone()


def load_sessions(p, active: dict | None = None) -> list[dict]:
    """Load all top-level Antigravity CLI sessions belonging to project `p`."""
    handoff_ref = None
    if p.handoffs:
        rel = os.path.relpath(p.handoffs, p.root).replace(os.sep, "/")
        handoff_ref = re.compile(re.escape(rel) + r"/([\w.-]+\.md)")

    db_path = os.path.join(p.app_data_dir, "conversation_summaries.db")
    by_id = {}

    if os.path.isfile(db_path):
        con = None
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
            con.row_factory = sqlite3.Row
            rows = con.execute("SELECT * FROM conversation_summaries").fetchall()
        except sqlite3.Error:
            rows = []
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
            parent_id = (row["parent_conversation_id"] or "").strip() if "parent_conversation_id" in keys else ""
            nesting = (row["nesting_depth"] or 0) if "nesting_depth" in keys else 0
            if parent_id or nesting > 0:
                continue
            ws_raw = row["workspace_uris"] if "workspace_uris" in keys else ""
            if not _workspace_belongs_to(ws_raw, p.root):
                continue

            row_app_dir = (row["app_data_dir"] or "").strip() if "app_data_dir" in keys else ""
            tpath = transcript_path_for(p.app_data_dir, cid, row_app_dir=row_app_dir, full=False)
            stats = read_transcript_stats(tpath, handoff_ref=handoff_ref)

            title = sanitize_cell(row["title"] if "title" in keys else "")
            step_count = (row["step_count"] or 0) if "step_count" in keys else 0
            db_last = parse_iso_local(row["last_modified_time"] if "last_modified_time" in keys else "")
            status = (row["status"] or "") if "status" in keys else ""
            not_fully_idle = bool(row["not_fully_idle"]) if "not_fully_idle" in keys else False

            prompts = stats["prompts"]
            if prompts == 0 and step_count > 0 and not os.path.isfile(tpath):
                # Transcript missing/rotated while DB shows activity; avoid misclassifying as empty.
                prompts = 1

            t0 = stats["t0"] or db_last or _fallback_mtime(p.app_data_dir, cid, tpath)
            t1 = stats["t1"] or db_last or t0
            if t1 < t0:
                t1 = t0

            by_id[cid] = {
                "id": cid,
                "title": title,
                "first_prompt": sanitize_cell(stats["first_prompt"] or ""),
                "name": title or sanitize_cell(stats["first_prompt"] or "") or "(no name)",
                "prompts": prompts,
                "step_count": step_count,
                "t0": t0,
                "t1": t1,
                "refs": stats["refs"],
                "transcript": tpath,
                "row_app_dir": row_app_dir,
                "status": status,
                "not_fully_idle": not_fully_idle,
            }

    # Fold in active hook session in case conversation_summaries.db hasn't flushed yet.
    if active and active.get("id"):
        cid = active["id"].strip()
        event = active.get("event") or ""
        tpath = active.get("transcript_path") or transcript_path_for(p.app_data_dir, cid)
        stats = read_transcript_stats(tpath, handoff_ref=handoff_ref)
        now = datetime.now().astimezone()
        existing = by_id.get(cid)
        prompts = stats["prompts"]
        if prompts == 0 and event in ("PreInvocation", "Stop"):
            prompts = max(existing["prompts"] if existing else 0, 1)
        elif existing:
            prompts = max(prompts, existing["prompts"])

        t0 = stats["t0"] or (existing["t0"] if existing else now)
        t1_candidates = [dt for dt in (stats["t1"], existing["t1"] if existing else None) if dt is not None]
        if event in ("PreInvocation", "Stop"):
            t1_candidates.append(now)
        t1 = max(t1_candidates) if t1_candidates else t0

        title = existing["title"] if existing else ""
        first_prompt = sanitize_cell(stats["first_prompt"] or (existing["first_prompt"] if existing else ""))
        refs = set(stats["refs"]) | (existing["refs"] if existing else set())
        by_id[cid] = {
            "id": cid,
            "title": title,
            "first_prompt": first_prompt,
            "name": title or first_prompt or "(no name)",
            "prompts": prompts,
            "step_count": existing["step_count"] if existing else prompts,
            "t0": t0,
            "t1": t1,
            "refs": refs,
            "transcript": tpath,
            "row_app_dir": existing["row_app_dir"] if existing else "",
            "status": existing["status"] if existing else "",
            "not_fully_idle": existing["not_fully_idle"] if existing else False,
        }

    sessions = list(by_id.values())
    sessions.sort(key=lambda s: (s["t0"], s["id"]))
    return sessions


def read_handoffs(p) -> list[dict]:
    if not p.handoffs or not os.path.isdir(p.handoffs):
        return []
    notes = []
    for f in sorted(glob.glob(os.path.join(p.handoffs, "*.md"))):
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read(3000)
        except OSError:
            continue
        first_line = text.split("\n", 1)[0]
        title = re.search(r'"([^"]+)"', first_line)
        m = re.search(r'Written (\d{4}-\d\d-\d\d) (?:at the end of|during) session "([^"]+)"', text)
        notes.append(
            {
                "path": f,
                "opens": title.group(1).split(":")[0] if title else None,
                "written": m.group(1) if m else os.path.basename(f)[:10],
                "by": m.group(2) if m else None,
            }
        )
    return notes


def assign(s: dict, groups: list[dict]):
    """Return (group key, claiming entry tuple or None)."""
    id8 = s["id"][:8].lower()
    for g in groups:
        for entry in g.get("ids", []):
            if str(entry).lower() == id8:
                return g["key"], (g["key"], entry)
    title = s.get("title") or ""
    for g in groups:
        if title and re.match(re.escape(g["key"]) + r"\s*:", title, re.I):
            return g["key"], None
    norm_title = norm(title) if title else ""
    if norm_title:
        for g in groups:
            for pattern in g.get("names", []):
                if norm(pattern) and norm(pattern) in norm_title:
                    return g["key"], (g["key"], pattern)
    return None, None


def classify(sessions: list[dict], groups: list[dict]):
    """Split sessions into by_group (with prompts), empty (0 prompts), and used rule entries."""
    empty = [s for s in sessions if s["prompts"] == 0]
    by_group, used = {}, set()
    for s in sessions:
        if s["prompts"] > 0:
            s["group"], entry = assign(s, groups)
            by_group.setdefault(s["group"], []).append(s)
            if entry is not None:
                used.add(entry)
    return by_group, empty, used


def checks(sessions: list[dict], groups: list[dict], used: set) -> list[str]:
    """Report group rules that match nothing and `<word>: ...` titles with no matching group."""
    found = [
        f"`{g['key']}`: {'ids' if e in g.get('ids', []) else 'names'} entry {e!r} claims no session "
        "(stale, or covered by an earlier entry)"
        for g in groups
        for e in list(g.get("ids", [])) + list(g.get("names", []))
        if (g["key"], e) not in used
    ]
    keys = {g["key"].lower() for g in groups}
    for s in sessions:
        m = KEYED.match(s.get("title") or "")
        if s["prompts"] > 0 and m and m.group(1).lower() not in keys:
            dest = f"`{s['group']}`" if s.get("group") else "Ungrouped"
            found.append(
                f"{label(s)} (`{s['id'][:8]}`): no group `{m.group(1)}` "
                f"(a typo, or a group still to add); filed under {dest}"
            )
    return found


def link(p, path: str, text: str) -> str:
    rel = os.path.relpath(path, os.path.dirname(p.index)).replace(os.sep, "/")
    return f"[{text}]({rel})"


def label(s: dict) -> str:
    return f"**{s['title']}**" if s.get("title") else f"*{s['name']}*"


def dumps(p, s: dict) -> list[str]:
    if not p.transcripts:
        return []
    return sorted(glob.glob(os.path.join(p.transcripts, f"*_{s['id'][:8]}_*.md")))


def row(p, s: dict) -> str:
    d0, d1 = f"{s['t0']:%m-%d}", f"{s['t1']:%m-%d}"
    dates = d0 if d0 == d1 else f"{d0} – {d1}"
    cells = [dates, label(s), str(s["prompts"]), f"`{s['id']}`"]
    if p.transcripts:
        cells.append(", ".join(link(p, f, os.path.basename(f)) for f in dumps(p, s)))
    return "| " + " | ".join(cells) + " |"


def handoff_rows(p, notes: list[dict], sessions: list[dict]) -> list[str]:
    named = {norm(s["title"]): s for s in sessions if s.get("title")}
    rows = []
    for h in notes:
        by = named.get(norm(h["by"])) if h["by"] else None
        out = f"{label(by)} (`{by['id'][:8]}`)" if by else (f"{h['by']} (not found)" if h["by"] else "")
        into = (
            [named[norm(h["opens"])]]
            if h["opens"] and norm(h["opens"]) in named
            else [s for s in sessions if os.path.basename(h["path"]) in s["refs"]]
        )
        rows.append(
            f"| {link(p, h['path'], os.path.basename(h['path']))} | {h['written']} | {out} | "
            + "; ".join(f"{s['t0']:%Y-%m-%d}" for s in into)
            + " | "
            + "; ".join(f"{label(s)} (`{s['id'][:8]}`)" for s in into)
            + " |"
        )
    return rows


def build(p, active: dict | None = None):
    """Build (markdown_text, one_line_summary, checks_list)."""
    sessions = load_sessions(p, active=active)
    notes = read_handoffs(p)
    by_group, empty, used = classify(sessions, p.groups)
    found = checks(sessions, p.groups, used)

    now = datetime.now().astimezone()
    cfg_note = f"Groups and rules: `{CONFIG}`." if p.has_config else f"Run `agy-sessions init` to add `{CONFIG}` for groups."
    lines = [
        f"# Antigravity CLI sessions for {os.path.basename(p.root)}",
        "",
        f"Written by `agy-sessions index` on {now:%Y-%m-%d %H:%M %Z} on `{p.host}` from `{tilde(p.app_data_dir)}` "
        f"({len(sessions)} sessions). {cfg_note} Auto-generated; do not edit manually.",
        "",
        "Resume one with `agy --conversation <session ID>` (or `/resume <short ID>` inside `agy`). "
        "Bold: session title; italics: first prompt fallback. Prompts: user messages sent to the agent. "
        "Dates in local time.",
        "",
    ]

    head = "| Dates | Name | Prompts | Session ID |" + (" Transcript dumps |" if p.transcripts else "")
    rule = "|---|---|--:|---|" + ("---|" if p.transcripts else "")

    for g in p.groups + [{"key": None, "title": "Ungrouped"}]:
        ss = by_group.get(g["key"])
        if not ss:
            continue
        key = f" (`{g['key']}`)" if g["key"] else ""
        d0, d1 = f"{ss[0]['t0']:%Y-%m-%d}", f"{max(s['t1'] for s in ss):%Y-%m-%d}"
        span = d0 if d0 == d1 else f"{d0} – {d1}"
        n = f"{len(ss)} session{'s' if len(ss) > 1 else ''}"
        lines += [f"## {g['title']}{key}: {n}, {span}", "", head, rule] + [row(p, s) for s in ss] + [""]

    if notes:
        lines += (
            [
                f"## Hand-off notes: {len(notes)}",
                "",
                "| Note | Out: written | Out: by session | In: picked up | In: by session |",
                "|---|---|---|---|---|",
            ]
            + handoff_rows(p, notes, [s for s in sessions if s["prompts"] > 0])
            + [""]
        )

    if empty:
        lines += [
            f"## Without prompts: {len(empty)}",
            "",
            "Opened and left without sending a prompt: "
            + ", ".join(f"`{s['id'][:8]}` ({s['t0']:%m-%d})" for s in empty)
            + ".",
            "",
        ]

    lines += ["## Checks", ""] + ([f"- {c}" for c in found] or ["Nothing to report."]) + [""]

    counts = ", ".join(f"{g['key']} {len(by_group[g['key']])}" for g in p.groups if g["key"] in by_group)
    summary = (
        f"{p.index}: {counts or 'no groups'}; ungrouped {len(by_group.get(None, []))}; "
        f"without prompts {len(empty)}"
    )
    return "\n".join(lines), summary, found


def _body_without_timestamp(text: str) -> str:
    """Strip the volatile HH:MM portion of the header line for change detection."""
    lines = text.splitlines()
    if len(lines) >= 3:
        lines[2] = re.sub(r"on \d{4}-\d{2}-\d{2} \d{2}:\d{2} [^\s]+ on ", "on <TS> on ", lines[2], count=1)
    return "\n".join(lines)


def write(p, active: dict | None = None, force: bool = False):
    """Write the index file atomically; skip disk write when body is unchanged unless `force`."""
    text, summary, found = build(p, active=active)
    if not force and os.path.isfile(p.index):
        try:
            with open(p.index, "r", encoding="utf-8") as f:
                old_text = f.read()
            if _body_without_timestamp(old_text) == _body_without_timestamp(text):
                return summary, found
        except OSError:
            pass

    out_dir = os.path.dirname(p.index)
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".sessions.", suffix=".md", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_path, p.index)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise
    return summary, found
