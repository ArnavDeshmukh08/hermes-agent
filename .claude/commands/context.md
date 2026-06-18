# /context — measure the per-turn token budget and shrink it under 12k

**Purpose:** Quantify Hermes's #1 instability — the ~17k-token per-turn overhead that
blows past Groq's 12k TPM and 413s every substantive turn — and report concrete cuts to
get interactive turns under budget. Wraps the `context-compressor` + `skills-pruner` skills.

## Usage
`/context` — measures the current per-turn budget and prints the cut plan.

## What it does
1. Invokes `skills/context-compressor.md` to estimate the per-turn request size
   (system + SOUL + skills-hub + memory + tools) against the **12k TPM** Groq budget.
2. Invokes `skills/skills-pruner.md` to itemize the skills-hub overhead (~10.6k, the
   dominant term) and identify which installed skill packs are unused and safe to prune.
3. Reports each contributor's token cost and a prioritized cut list to reach < 12k —
   prune unused packs and/or gate the hub behind `tools.tool_search`, or route heavy
   work off Groq (Option B). Cross-checks recent 413s in `~/.hermes/logs/errors.log`.

## Output
A budget breakdown (per-contributor tokens vs 12k) + a prioritized cut plan with the
expected post-cut turn size. Maps to ROADMAP Phase 0, item 1.

## Boundaries / Note
Read-only — measures and recommends, never edits. Applying any cut (pruning packs,
toggling `tools.tool_search`, config edits) is a live change: hand it to `/deploy`
(backup + validate + approval). Phase 0 sequencing: this comes first.
