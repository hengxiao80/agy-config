# Global Agent Guidelines

## Git Operations

- **Commits & pushes:** Only run `git commit` or `git push` when the user explicitly asks or approves.
- **Terminal sandbox & `.git/` writes:** When the terminal sandbox is enabled (e.g., macOS Seatbelt), writes to `.git/` are blocked inside the sandbox. Run mutating `git` commands (such as `git add`, `git commit`, `git checkout`, `git mv`, `git rm`, `git stash`) with `BypassSandbox: true` directly rather than attempting them sandboxed first; keep read-only `git` commands (`git status`, `git diff`, `git log`) sandboxed.
