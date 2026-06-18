# Skill: Context Compressor

**Responsibility:** Diagnose and reduce per-turn context size so interactive turns fit
under the provider budget. This is the deep-dive for the #1 instability
(`docs/AUDIT.md`). Replaces the old `context-manager` stub.

## The problem in one line
Fixed per-turn overhead ≈ **17k tokens** > Groq free **12k TPM** → every substantive
turn returns 413, the compressor can't shrink *fixed* context, the session auto-resets.

## Token budget (measured 2026-06-15)
| Component | ~Tokens | Lever |
|---|---:|---|
| **Skills-hub prompt** (`.skills_prompt_snapshot.json`, 73 skills/18 packs) | **~10,642** | **prune packs / make on-demand via `tool_search`** |
| base + inline system prompt | ~2–3k | tighten `agent.system_prompt` |
| tool schemas (5 toolsets) | ~1.5–3k | small win only |
| SOUL.md | ~1,950 | load active persona only; trim |
| USER.md | ~294 | fine |
| memory injection | ~900 | keep `memory_char_limit` modest |

## Diagnose (read-only)
1. Confirm the symptom: `grep -E "413|Requested|cannot compress" ~/.hermes/logs/agent.log`.
2. Measure overhead: `wc -c ~/.hermes/.skills_prompt_snapshot.json ~/.hermes/SOUL.md
   ~/.hermes/USER.md` (÷4 ≈ tokens).
3. Rule out red herrings: AGENTS.md is **not** loaded (gateway cwd `~/.hermes` has none);
   toolset trimming does **not** help (skills load independently). Don't chase these.

## Reduce (live changes — backup + validate; out of scope for the audit mission)
1. **Skills, biggest lever:** prune installed packs to those in use; and/or set the
   skills hub to on-demand retrieval via `tools.tool_search` instead of full injection.
   Target overhead **< 8k**.
2. **Persona:** keep SOUL.md tight; load only the active persona's section.
3. **Memory:** keep injection limits modest.
4. **Re-validate:** send `het` in the target chat → log must show `Requested < 12000` and
   a real reply (not a 218-char error + reset).

## Don't
- Don't rely on the compressor to fix this — it only acts on conversation history, not
  fixed system context.
- Don't trim toolsets expecting a token win.

## Output
Before/after token budget + the specific config levers pulled + the re-validation log line.
