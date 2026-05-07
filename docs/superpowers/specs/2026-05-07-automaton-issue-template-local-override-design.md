# Automaton issue-template local override

**Date:** 2026-05-07
**Status:** Draft for review
**Author:** Maksym Panibratenko (with Claude)

## Context

The `automaton` plugin (v0.1.1) ships a strict dry-run gate in `automaton:interpreting-an-issue` Step 1: the issue body must contain `## Goal`, `## Acceptance criteria`, AND `## Verification`. Missing any one halts the worker with a `claude:blocked` reason "spec template incomplete" before the interpreter agent even runs.

This rejected issue #99 ("Settings page: visibility / contrast issues + dropdown redesign needed"), which was filed in good faith using a perfectly clear bug-report structure (`## Symptoms` / `## Hypotheses to investigate` / `## Suggested approach` / `## Acceptance`). The interpreter would have had no trouble with it. The header-name pre-filter rejected it on a formality.

External signal: the Anthropic / charlielab agent ecosystem has converged on different issue/PR templates than the one automaton enforces. Locking ourselves to a single naming convention is friction without payoff — the interpreter agent already has its own ambiguity gate (`ambiguity_score >= 2`) and complexity gate (`estimated_complexity == "large"`), which catch the *real* problems (vague intent, oversized scope) instead of the cosmetic ones (header capitalization).

## Decision

Add a **local override** to this repo's `CLAUDE.md` that supersedes the plugin's strict header check. The plugin source is unchanged — the override leverages the harness's documented instruction priority:

1. User instructions (CLAUDE.md, GEMINI.md, AGENTS.md, direct requests) — highest
2. Superpowers / plugin skills — override default system behavior
3. Default system prompt — lowest

When `automaton:interpreting-an-issue` runs in this repo, both the skill body and this repo's CLAUDE.md are in the worker's context. Per the priority hierarchy, the CLAUDE.md override wins.

This pattern — local repo overrides plugin defaults via CLAUDE.md — is established here as the convention for all future plugin-rule overrides in this repo. The first override happens to be the issue-template gate; siblings (auto-merge patterns, etc.) can follow the same shape without restructuring.

## Why CLAUDE.md, not `.claude-harness.toml`

The TOML route would require a one-line change to the upstream plugin SKILL.md (read the new config key) and a plugin version bump. That's coupling: a "local" override that requires editing two repos isn't local. The CLAUDE.md route requires zero changes outside this repo and is immediately effective.

The trade-off is that CLAUDE.md is "soft" enforcement (prompt-priority, model-mediated) rather than "hard" enforcement (a code path that runs unconditionally). For a worker that always loads CLAUDE.md into context — which all current automaton flows do — this is reliable. If we ever build a fully unattended path that bypasses context loading, we can graduate to the TOML mechanism then.

## The override

The CLAUDE.md addition replaces Step 1 of `automaton:interpreting-an-issue` with a relaxed check.

### Goal slot (any one suffices)

- `## Goal`
- `## Symptoms`
- `## Problem`

### Acceptance slot (any one suffices)

- `## Acceptance criteria`
- `## Acceptance Criteria`
- `## Acceptance`

### Verification slot

Optional. If the issue has a `## Verification` block, Step 5 runs those commands as before. If absent, Step 5 falls back to the project default verification suite, picked by which paths the implementation touches:

| Touched path | Fallback verification command |
|---|---|
| `frontend/**` only | `cd frontend && npm run typecheck && npm run test -- --run` |
| `app/**` or `tests/**` only | `uv run pytest tests/unit/` |
| both | both, in order: backend first, then frontend |
| neither (e.g., docs-only) | no command — skip Step 5, Step 6 lands directly |

### What stays strict

The interpreter agent's own halt gates (Step 7) are unchanged:

- `ambiguity_score >= 2` → halt with `claude:blocked`
- `estimated_complexity == "large"` → halt with `claude:blocked`

These are the *real* gates. The header check was a dumb pre-filter that double-counted the wrong thing.

## Mechanics

### CLAUDE.md placement

Append a new top-level section `## Automaton overrides (local, supersede plugin defaults)` at the end of `CLAUDE.md`. Existing top-down structure (Setup → Tests → Non-obvious behaviors → Hard limits) is preserved. Override sections are clearly demarcated as instructions to Claude, not as documentation for humans.

### Self-contained format

Each override is its own subsection (`### <skill-name>: <what changes>`). The first paragraph is a directive ("**Override:** when running `<skill>` in this repo, replace the …"); the rest is the relaxed rule. A short rationale sentence explains *why* it's looser, so future-me reading the override knows whether the loosening still applies when the underlying plugin changes.

### Issue #99 unblock path

After the override lands, `/work-issue 99` should pass the dry-run gate without needing to edit the issue body. The interpreter agent will read the issue, decide it's interpretable, and continue. (The issue body's `## Acceptance` block is already a clear acceptance criterion.)

## Out of scope

- Upstream plugin changes (no PR to `maksym-panibrat/automaton`).
- A general "all repos override plugins" mechanism — this is just this repo's convention.
- Changing the interpreter agent's prompt or its halt thresholds.
- A `.claude-harness.toml` `[issue_template]` stanza — explicitly deferred until/unless prompt-priority proves unreliable.

## Verification

After landing the override:

1. Re-run `/work-issue 99` in this repo. Expected: no halt at Step 3; the interpreter runs and posts a dry-run interpretation comment.
2. Inspect the comment. Expected: a structured interpretation with non-empty `interpretation`, `files_to_touch`, `verification_plan`, `ambiguity_score`, `estimated_complexity`.
3. The worker either continues to Step 4 (if `ambiguity_score < 2` and `estimated_complexity != "large"`) or halts on those *real* gates with a meaningful message.

No automated tests are added — the override is a CLAUDE.md edit, and its effect is observable through the next `/work-issue` invocation.
