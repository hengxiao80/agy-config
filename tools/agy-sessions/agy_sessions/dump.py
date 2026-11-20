"""Dump an Antigravity CLI session transcript (JSONL) to Markdown.

Prefers `transcript_full.jsonl` (untruncated) when available, falling back to
`transcript.jsonl`. Keeps user requests, assistant responses, tool calls
(name + args), tool results, and error messages; leaves out thinking blocks and
internal system reminders.

Output: `<transcripts>/<YYYY-MM-DD_HHMMSS>_<session id[:8]>_<label>.md` (UTC).
"""

from datetime import datetime, timezone
import json
import os

from .index import extract_user_request


def _safe_fence(text: str) -> str:
    return text.replace("```", "` ` `")


def _format_tool_calls(tool_calls: list) -> list[str]:
    blocks = []
    for tc in tool_calls or []:
        if not isinstance(tc, dict):
            continue
        name = tc.get("name") or "tool"
        args = tc.get("args", {})
        # Args values in agy transcripts may be JSON-encoded strings; pretty-print cleanly.
        pretty_args = {}
        if isinstance(args, dict):
            for k, v in args.items():
                if isinstance(v, str):
                    try:
                        pretty_args[k] = json.loads(v)
                    except ValueError:
                        pretty_args[k] = v
                else:
                    pretty_args[k] = v
        else:
            pretty_args = args
        dumped = json.dumps(pretty_args, indent=2, ensure_ascii=False)
        blocks.append(f"**Tool call: {name}**\n\n```json\n{_safe_fence(dumped)}\n```")
    return blocks


def convert(path: str, session: str, label: str, out_dir: str, title: str = "") -> str:
    now = datetime.now(timezone.utc)
    parts = [f"# Session transcript {session}", ""]
    if title:
        parts.append(f"- Title: {title}")
    parts += [
        f"- Source: `{path}`",
        f"- Dumped: {now:%Y-%m-%d %H:%M:%S} UTC ({label})",
        "",
    ]

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue

            step_type = d.get("type") or ""
            source = d.get("source") or ""
            ts = d.get("created_at") or ""

            if step_type == "USER_INPUT" and (not source or source == "USER_EXPLICIT"):
                body = extract_user_request(d.get("content") or "")
                if body:
                    parts += [f"## User — {ts}", "", body, ""]
            elif step_type == "PLANNER_RESPONSE":
                blocks = []
                content = (d.get("content") or "").strip()
                if content:
                    blocks.append(content)
                blocks.extend(_format_tool_calls(d.get("tool_calls") or []))
                if blocks:
                    model = d.get("model") or ""
                    head = f"## Assistant ({model}) — {ts}" if model else f"## Assistant — {ts}"
                    parts += [head, "", "\n\n".join(blocks), ""]
            elif step_type == "GENERIC" and source == "MODEL":
                content = (d.get("content") or "").strip()
                if content:
                    parts += [
                        f"### Tool result — {ts}",
                        "",
                        f"```\n{_safe_fence(content)}\n```",
                        "",
                    ]
            elif step_type == "ERROR_MESSAGE":
                err = (d.get("error") or d.get("content") or "").strip()
                if err:
                    parts += [f"## Error — {ts}", "", f"```\n{_safe_fence(err)}\n```", ""]

    os.makedirs(out_dir, exist_ok=True)
    safe_label = "".join(c if c.isalnum() or c in "-_" else "-" for c in (label or "manual")).strip("-") or "manual"
    out = os.path.join(out_dir, f"{now:%Y-%m-%d_%H%M%S}_{session[:8]}_{safe_label}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return out
