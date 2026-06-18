# Skill: Skills Pruner

**Responsibility:** The concrete operating procedure to shrink the skills-hub prompt under
budget — measure the snapshot, find which packs/skills are actually used, prune unused ones
and/or switch the hub to on-demand via `tools.tool_search`, target fixed overhead < 8k.
This **operationalizes** the fix `context-compressor` diagnoses; it does not re-measure the
whole turn budget or touch SOUL/memory/tool-schema levers (those stay in `context-compressor`).

## Why this is the highest-leverage fix
The skills-hub prompt is ~**10,642 tok** (`~/.hermes/.skills_prompt_snapshot.json`,
42,569 bytes, **73 skills / 18 packs**) — the single largest slice of the ~17k fixed
per-turn overhead. Get this under ~3k and the per-turn budget drops below 12k TPM, which is
what stops the 413 → reset loop. The other levers (SOUL ~2k, schemas ~1.5–3k) are small wins
by comparison.

## Method  (read-only first; live edits need backup + approval + validation)
1. **Measure current cost (read-only):**
   - `wc -c ~/.hermes/.skills_prompt_snapshot.json` (÷4 ≈ tokens; baseline ~10.6k).
   - Enumerate packs/skills: `python -c "import json;d=json.load(open('/home/hermes/.hermes/.skills_prompt_snapshot.json'));print(len(d));print(json.dumps(d,indent=2)[:2000])"`
     to see the structure (packs → skills) before deciding what is prunable.
2. **Find what's actually used (read-only):** scan recent history for skill/pack invocations
   so pruning is evidence-based, not guessed:
   - `grep -hoE "skill[_-]?[a-z0-9_-]+" ~/.hermes/logs/agent.log | sort | uniq -c | sort -rn`
   - cross-check against the cron jobs (`~/.hermes/cron/jobs.json`) — a pack only a paused job
     used is a prune candidate.
   - Anything with **zero** hits across logs + cron + SOUL references is a prune candidate.
3. **Decide the lever (pick one, smallest blast radius first):**
   - **(A) On-demand hub (preferred, biggest + safest win):** stop full-injecting the hub;
     gate skills behind `tools.tool_search` so only the schema for a requested skill loads.
     This removes nearly all 10.6k from *every* turn while keeping all skills reachable.
   - **(B) Prune unused packs:** uninstall/disable packs with zero usage so the snapshot
     regenerates smaller. Use when on-demand isn't viable or you want both.
   - (A) and (B) compose — do (A) for the budget win, (B) for housekeeping.
4. **Apply (live — backup + approval; route through `deployment-validator`):**
   - Back up the snapshot and config:
     `cp ~/.hermes/.skills_prompt_snapshot.json{,.bak.$(date +%Y%m%d_%H%M%S)}` and the same
     for `~/.hermes/config.yaml`.
   - Set `tools.tool_search` on (config) and/or remove the unused packs.
   - Let the snapshot regenerate (restart the service) rather than hand-editing the JSON.
5. **Validate:**
   - `wc -c ~/.hermes/.skills_prompt_snapshot.json` → confirm the new size (target < ~12k
     bytes ≈ 3k tok if on-demand; fixed overhead target **< 8k** total).
   - `systemctl --user restart hermes-gateway.service` → `is-active`.
   - Send `het` in the target chat → log shows `Requested < 12000` and a real reply, no reset.
   - Exercise one on-demand skill to confirm `tool_search` still surfaces it.

## Don't
- Don't hand-edit `.skills_prompt_snapshot.json` as the fix — it's regenerated; change the
  source (config / installed packs) and let it rebuild.
- Don't trim toolsets expecting a token win — skills load independently of toolsets.
- Don't prune a pack a **paused** cron job will need once re-enabled (see `cron-router`);
  check `jobs.json` before deleting.
- Don't re-derive the full per-turn budget here — that's `context-compressor`.

## Output
Before/after snapshot size (bytes + ~tok), the usage table (pack/skill → hit count), which
lever was pulled (on-demand and/or which packs removed), the new fixed-overhead estimate, and
the `het` re-validation log line.
