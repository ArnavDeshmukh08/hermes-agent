# /roadmap — show the build plan and recommend the next action + owner

**Purpose:** Print the phased plan from `docs/ROADMAP.md` and recommend the single next
action with its owner (agent/skill), enforcing the Phase-0-first sequencing rule.

## Usage
`/roadmap` — shows phases + the recommended next step.

## What it does
1. Reads `docs/ROADMAP.md` and renders the phases (Phase 0 Stabilize → Phase 5 Outreach)
   with each item's status.
2. Applies the **sequencing rule**: Phase 0 (get interactive turns < 12k) must complete
   before anything above the deterministic path is treated as reliable — so the next
   action is always the first unfinished Phase 0 item until Phase 0 is done.
3. Recommends the next action and names its owner:
   - measure budget → `/context` (`context-compressor` + `skills-pruner`)
   - apply a Phase-0 cut → `/deploy` (`deployment-validator` + `infrastructure` agent)
   - scan for latent issues → `/hunt` (`bug-hunter` agent)
   - health snapshot → `/doctor` (`hermes-doctor`)

## Output
The rendered roadmap with statuses + a one-line "next action → owner" recommendation,
honest about what is blocked behind Phase 0.

## Boundaries / Note
Read-only. Recommends; it does not act. Live Phase-0 changes still go through `/deploy`
(backup + validate + approval).
