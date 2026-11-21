# Global Agent Guidelines

## Git Operations

- **Commits & pushes:** Only run `git commit` or `git push` when the user explicitly asks or approves.
- **Terminal sandbox & `.git/` writes:** When the terminal sandbox is enabled (e.g., macOS Seatbelt), writes to `.git/` are blocked inside the sandbox. Run mutating `git` commands (such as `git add`, `git commit`, `git checkout`, `git mv`, `git rm`, `git stash`) with `BypassSandbox: true` directly rather than attempting them sandboxed first; keep read-only `git` commands (`git status`, `git diff`, `git log`) sandboxed.

## Markdown Notes for Typora (Screen and Letter-Size PDF)

The user reads Markdown notes in Typora and often prints them with its *Export to PDF* on letter-size paper. Write every Markdown document meant for the user to read (notes, write-ups, plans, logs, reports) so that it renders correctly there and fits the printed page. This does not cover `AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, agent memory files, or Markdown whose format another tool dictates.

### Math

- **Every `$$` display block needs `\tag{n}` or `\notag`.** Typora has numbered untagged blocks, throwing off the manual numbering, even with "No Automatic Equation Number" selected. One `\tag`/`\notag` per block; in a multi-line block it goes after `\end{aligned}` / `\end{gathered}`.
- **Keep display equations narrow.** Split chains of several `\qquad`-joined parts or `\Longrightarrow` steps into `aligned`/`gathered` lines. Put remarks in the prose, not in `\text{...}` inside the equation. Avoid label columns (`&&\text{...}`) in `aligned`.
- Inline `$...$` math needs Typora's *Preferences → Markdown → Inline Math*; display math works by default.

### Tables

- **Keep tables narrow:** few columns, short cells, no long unbreakable cells (code-span lists, long inline math, "a/b" number pairs).
- Split a wide table (one per file, one per quantity), move definitions to a lead-in sentence, and use footnote marks (¹ ²) for long cell remarks.

### Prose

- **Don't hard-wrap prose:** one line per paragraph or list item; Typora wraps it to the page. Existing hard-wrapped files should be unwrapped too, but only when they are edited or saved anyway: then unwrap all of the file's prose, not only the new text. Don't unwrap files in bulk or on their own. This applies only to Markdown files created for the current project (by the user, by the agent, or by anyone else working on it). It does not apply to files of another project, loose files outside a project, or Markdown that came from elsewhere (upstream, vendored or cloned code, or documents copied in for reference).

### Diagrams in Code Blocks

- **At most ~72 characters per line, plain ASCII** (`|`, `v`, `->`), so they fit the page and can't lose their alignment to a font fallback.
- Don't split a text line that already fits, and use one `v` line per arrow; extra breaks there are unnecessary.

**Why:** wide equations and table rows ran off the printed page, untagged equations got stray numbers, and hard-wrapped prose and over-broken diagrams were unwanted.

## Answer Style

- **No LaTeX in chat answers.** The terminal doesn't render it. Derivations and equation-heavy explanations go into a Markdown note in the project (following the Typora Markdown guidelines above); the reply says where in the note to look and summarizes in words or plain Unicode (λ, σ², ≈). Small inline quantities in chat: Unicode, not `$...$`. If the project has no note that fits, ask where the derivation should go.
- **Short status lines need the same technical care as full answers.** Brief progress lines and one-sentence summaries before a tool call must name the right tool or program and the right quantity (a value vs. its tendency, say), with no compressed paraphrase that changes the meaning. Check every technical noun and verb in them; if there is no room to say it precisely, say less ("checking the tutorial script") rather than compress a finding.

**Why:** in `bnf_wrf_da`, the user said a LaTeX-heavy answer about a likelihood derivation was unreadable in the terminal and asked for it in the note instead (2026-10-27); and a one-line status attributed a DART program to WRFDA and called a tendency a value. The user reads the short lines closely and relies on them (2026-10-02).

## Working Style

- **Confirm before commits, pushes, and production runs.** The user reviews changes before a git commit or push, and before a production run or production plots (a full or expensive regeneration of real outputs, as opposed to a small test run that checks a change). Ask for a go-ahead before each, even after a series of changes that "should" be ready. When a change affects how plots look, the plan includes a few rendered test images, shown before any batch job.
- **Follow the step order the user sets.** When the user lays out steps with ordering or gating ("commit after the smoke test and before the production run"), each gate is a hard stop: finish the gated step first, or stop and ask, rather than go on because an earlier step succeeded. Approving a plan in principle is not permission to run ahead of its order.
- **Verify before a speculative fix.** For intermittent or environment-level problems, gather direct evidence before changing code: application and extension logs, the state of the live process, a direct connection to the component in question, installed vs. latest software versions. Don't build a fix on one plausible piece of evidence or call a hypothesis confirmed after one good run; say what a test does and doesn't prove.

**Why:** all three came up in `bnf_wrf_eval`. The user stopped a notebook re-run they had approved too hastily and asked to review changes before commits and production runs/plots, with test images first (2026-09-01). A production job array went out right after a smoke test, before the commit the user had asked for in between (2026-08-30). Two code changes for an intermittent Jupyter hang rested on one plausible clue each; the cause, an outdated VS Code Jupyter extension, turned up only from logs and from inspecting the live kernel (2026-08-27).
