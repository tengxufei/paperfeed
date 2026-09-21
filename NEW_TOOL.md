# Starting a bigger research tool with Claude Code

The best-practices article's argument is that the prompt is the cheap part
and the harness is the moat. So this is in three parts: set the harness up
first, then send a plain prompt, then work in the loop. The prompt is last
on purpose.

---

## 1. The harness, before you type anything

You already run two of the six layers: a global `~/.claude/CLAUDE.md` and
52 global skills. These are the ones worth adding per project.

### CLAUDE.md at the project root

Pulled into every session automatically. Put in it only what Claude would
otherwise rediscover each time: the commands, where code lives, the
conventions you actually enforce, and the traps.

**Keep it short.** Every standing instruction spends attention on every turn.
Aim for ~40 lines. `CLAUDE.md` in this repo is a worked example — copy its
shape, not its content.

Two shortcuts: `/init` drafts one from the codebase, and prefixing a message
with `#` writes that instruction into it, so it grows as you correct Claude.

### A permissions policy

`.claude/settings.json` with `allow` / `deny` / `ask` lists. Allowlist the
read-only commands you run constantly so you stop approving `ls`; keep
destructive ones denied. The file in this repo is a starting point.

The realistic risk with a broad allowlist is not a typo — it is an
instruction hidden in a web page or a downloaded file chaining an allowed
command into something you did not intend. Keep `deny` real.

### Plan mode

Shift+Tab into plan mode for anything non-trivial. Claude works read-only and
hands you a plan to approve. This is the single habit that most reduces
"confidently built the wrong thing".

### Later, once the shape is clear

- **Skills** (`~/.claude/skills/<name>/SKILL.md`) for workflows you repeat:
  "ingest a new source", "add a chart", "write the weekly summary".
- **Hooks** in settings.json for things that must happen *every* time — the
  harness runs them, so unlike an instruction they cannot be skipped.
- **Subagents** for breadth (searching, scanning many files), not depth.
  Reasoning-heavy work belongs inline where you can steer it.

---

## 2. The prompt

Plain on purpose. Everything else gets added in conversation, one piece at a
time, once you can see what the tool actually returns.

---

We're building a research tool. I'm a researcher, not a strong coder: explain
choices in plain language and keep the design as simple as it can be while
still working.

**What it is.** I give it the topics I follow. It collects papers on them
continuously, keeps them, and helps me understand what my field is doing —
not just alerting me, but supporting real analysis on top of the collection.

**Where I want to end up**, so you can design toward it rather than bolting
it on later:

- a paper collection that keeps growing on its own,
- a visual dashboard I actually read,
- real data analysis over the collection: what is rising, what connects to
  what, who is publishing, how a period compares to another.

**Build it in this order**, and stop after each for me to look at it:

1. **Collect.** Fetch papers matching my topics from PubMed and the preprint
   servers, deduplicate by DOI, and remember what I have already seen so
   nothing repeats. Write the results to a local HTML file. Run on a
   schedule I can change from a config file.
2. **Keep.** Papers accumulate into a local store that grows by itself,
   with a way for me to mark the ones I care about.
3. **Show.** A dashboard over the collection, rebuilt on every run.
4. **Analyse.** Let me search any date range I choose, not only recent days,
   and compare periods.

**Hard constraints**

- Python, standard library + `requests` only. No chart library, no web
  framework, no AI SDK.
- Config-driven: everything I might change lives in one config file I edit.
- Always write the local file before attempting anything that can fail
  (email, network, AI). That file is how we check it worked.
- Secrets come from environment variables, never from a file in the project.
- Any AI features are optional and off by default; the tool must be fully
  useful with no API key.

**How I want you to work**

- Plan first and wait for my approval before writing code.
- Verify by running it and opening the output, not by reading the code back
  to me. Tell me what you actually observed.
- When you find a bug, say what it was and how you proved it is fixed.
- Do not quote API prices or model names from memory. Check them.
- If you are unsure, say so rather than guessing.

---

## 3. Working in the loop

**Explore → plan → code → commit.** The first two are the ones people skip,
and skipping them is exactly when an agent builds the wrong thing
confidently.

**Course-correct early.** Escape interrupts; double-Escape rewinds to an
earlier point. Catching a wrong turn in the first thirty seconds beats
untangling the result.

**Keep the context clean.** `/clear` between unrelated tasks. A long session
fills with stale file contents and quality drifts with it. Reference files
precisely with `@` instead of letting Claude grep the tree.

**Commit per feature**, not per session, so any one change can be reverted
without losing the rest.

---

## What to add through conversation, not up front

Raise these one at a time, after you can see real output:

relevance ranking with visible reasons · filters and muted terms · following
specific authors · a local page for browsing and saving · charts and a
knowledge graph · trend alerts · optional AI summarisation · email delivery
