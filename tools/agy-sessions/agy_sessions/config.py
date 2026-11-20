"""Project configuration (.agy-sessions.toml) and Antigravity CLI storage paths.

The project root is the nearest directory at or above the current one that holds
`.agy-sessions.toml` (preferred opt-in marker). For backward compatibility, a
directory containing `agy/` (walking up to `.git`, `$HOME`, or `/`) is also
recognized with default paths and no groups.

Paths in `[paths]` are relative to the project root and may contain `{host}`,
which expands to the machine's short hostname (`hostname -s`).
"""

import os
import socket
import tomllib

CONFIG = ".agy-sessions.toml"
RESERVED = ("empty", "ungrouped")
DEFAULT_PATHS = {
    "index": "agy/{host}/sessions.md",
    "transcripts": "agy/{host}/transcripts",
    "handoffs": "agy/handoffs",
}

SKELETON = """\
# Antigravity CLI sessions of this project, for agy-sessions.
# `agy-sessions index`, run by the personal-kit hooks, writes the index file;
# delete this file (and agy/ if unused) to turn session tracking off here.
#
# A session goes to the first of these that claims it:
#   1. a group whose `ids` holds the first 8 characters of its session ID;
#   2. a group whose key starts its session title ("setup: part 2" goes
#      to `setup`); groups exist only here, a title never creates one;
#   3. the first group, in file order, with a `names` entry found in the
#      session title (case, spaces and punctuation ignored).
# Sessions nobody claims are listed under "Ungrouped"; sessions without a
# single prompt are listed under "Without prompts". Groups appear in this order.

[paths]           # relative to this file; {host} = short hostname; "" disables
index = "agy/{host}/sessions.md"
transcripts = "agy/{host}/transcripts"
handoffs = "agy/handoffs"

# [[group]]
# key = "setup"
# title = "Project setup"
# names = ["setup"]

[[group]]
key = "junk"
title = "Redundant or stray"
# Stray sessions go here by 8-char ID, then: agy-sessions delete --group junk
ids = []
"""


class ConfigError(Exception):
    pass


def short_hostname() -> str:
    return (socket.gethostname().split(".")[0] or "default").strip()


def default_app_data_dir() -> str:
    override = os.environ.get("AGY_CONFIG_DIR")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.abspath(os.path.expanduser("~/.gemini/antigravity-cli"))


def derive_app_data_dir(transcript_path: str = "") -> str:
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
    return default_app_data_dir()


def tilde(path: str) -> str:
    """Return `path` with the home directory collapsed to `~`."""
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def find_toml_root(start: str):
    """Nearest directory at or above `start` containing `.agy-sessions.toml`, or None."""
    d = os.path.realpath(start)
    while not os.path.isfile(os.path.join(d, CONFIG)):
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return d


def find_legacy_agy_root(start: str):
    """Fallback opt-in: nearest directory with an `agy/` folder up to `.git`, `$HOME`, or `/`."""
    home = os.path.realpath(os.path.expanduser("~"))
    cur = os.path.realpath(start)
    while True:
        if cur in ("/", home):
            return None
        if os.path.isdir(os.path.join(cur, "agy")):
            return cur
        if os.path.exists(os.path.join(cur, ".git")):
            return None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def find_root(start: str, allow_legacy: bool = True):
    """Return (root_dir, has_toml) for `start`, or (None, False)."""
    toml_root = find_toml_root(start)
    if toml_root:
        return toml_root, True
    if allow_legacy:
        legacy = find_legacy_agy_root(start)
        if legacy:
            return legacy, False
    return None, False


def _expand_host(template: str, host: str) -> str:
    return template.replace("{host}", host) if template else ""


def gitignore_pattern(template: str, is_dir: bool = False) -> str:
    """Convert a `[paths]` template into a portable `.gitignore` line."""
    pat = template.replace("{host}", "*").strip("/")
    if is_dir and pat:
        pat += "/"
    return pat


class Project:
    """Project configuration rooted at `root`."""

    def __init__(self, root: str, app_data_dir: str = "", host: str = "", allow_legacy: bool = True):
        self.root = os.path.realpath(root)
        self.host = host or short_hostname()
        self.app_data_dir = os.path.abspath(app_data_dir) if app_data_dir else default_app_data_dir()
        self.config = os.path.join(self.root, CONFIG)
        self.has_config = os.path.isfile(self.config)

        if self.has_config:
            try:
                with open(self.config, "rb") as f:
                    cfg = tomllib.load(f)
            except tomllib.TOMLDecodeError as e:
                raise ConfigError(f"{self.config}: {e}") from None
        elif allow_legacy and os.path.isdir(os.path.join(self.root, "agy")):
            cfg = {}
        else:
            raise ConfigError(f"No {CONFIG} at {self.root}")

        raw_paths = cfg.get("paths", {})
        if not isinstance(raw_paths, dict):
            raise ConfigError(f"{self.config}: [paths] must be a table")
        unknown = set(raw_paths) - set(DEFAULT_PATHS)
        if unknown:
            raise ConfigError(
                f"{self.config}: unknown [paths] key(s): {', '.join(sorted(unknown))}"
            )
        self.path_templates = {**DEFAULT_PATHS, **raw_paths}
        if not self.path_templates["index"]:
            raise ConfigError(f"{self.config}: [paths] index is empty")

        def resolve(key: str):
            val = _expand_host(self.path_templates[key], self.host)
            return os.path.join(self.root, val) if val else None

        self.index = resolve("index")
        self.transcripts = resolve("transcripts")
        self.handoffs = resolve("handoffs")

        groups = cfg.get("group", [])
        if not isinstance(groups, list):
            raise ConfigError(f"{self.config}: [[group]] must be a list of tables")
        self.groups = groups
        keys = [g.get("key") for g in self.groups if isinstance(g, dict)]
        seen_lower = set()
        for g in self.groups:
            if not isinstance(g, dict) or not g.get("key") or not g.get("title"):
                raise ConfigError(f"{self.config}: a [[group]] without key or title: {g}")
            kl = g["key"].lower()
            if kl in RESERVED:
                raise ConfigError(f"{self.config}: group key {g['key']!r} is reserved")
            if kl in seen_lower or keys.count(g["key"]) > 1:
                raise ConfigError(f"{self.config}: group key {g['key']!r} is not unique")
            seen_lower.add(kl)

    @classmethod
    def find(cls, start: str = ".", app_data_dir: str = "", host: str = "", allow_legacy: bool = True):
        root, _ = find_root(start, allow_legacy=allow_legacy)
        if not root:
            return None
        return cls(root, app_data_dir=app_data_dir, host=host, allow_legacy=allow_legacy)


def add_gitignore(root: str, entries: list[str]) -> str:
    """Append missing entries to `root/.gitignore`; return a status message."""
    entries = [e for e in entries if e]
    if not entries:
        return "no .gitignore entries to add"
    ignore = os.path.join(root, ".gitignore")
    text = ""
    if os.path.exists(ignore):
        with open(ignore, "r", encoding="utf-8") as f:
            text = f.read()
    existing = set(text.splitlines())
    add = [x for x in entries if x not in existing]
    if not add:
        return f"{ignore} already has {', '.join(entries)}"
    sep = ("" if text.endswith("\n") or not text else "\n") + ("\n" if text.strip() else "")
    with open(ignore, "a", encoding="utf-8") as f:
        f.write(
            sep
            + "# Antigravity CLI session index and transcript dumps (agy-sessions)\n"
            + "\n".join(add)
            + "\n"
        )
    return f"added {', '.join(add)} to {ignore}"
