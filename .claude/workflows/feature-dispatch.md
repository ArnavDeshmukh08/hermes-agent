# Workflow: Feature Dispatch

**Purpose:** Turn an idea-dump into a reviewable spec, route it to the right execution path, and
return a reviewable diff behind an approval gate. Maps to `agents/dispatcher.md` + the Review Board
(`standards/review-checklist.md`). Foundation for the future dev-team dispatcher (ROADMAP Phase 4).

```
idea-dump ─▶ spec ─▶ route ──┬─ deterministic → no_agent script
                             ├─ heavy        → local Ollama (per-job override)
                             └─ interactive  → Groq 70B + 8B fallback
                                   │
                                   ▼
                    dispatch (delegation subagents / heavy path) ─▶ diff ─▶ [APPROVAL] ─▶ merge
```

## Steps
1. **Capture.** Take the idea-dump verbatim. Ask only the few questions that most reduce ambiguity.
2. **Spec** (`agents/dispatcher.md`). Write a short reviewable spec: goal · scope · acceptance ·
   out-of-scope. A partial spec beats a fabricated result.
3. **Route** by the three-path rule (`standards/engineering-standards.md` §4,
   `agents/architect.md`):
   - **Deterministic** (fixed pipeline, no reasoning) → `no_agent` script, zero LLM.
   - **Heavy** (codegen, long reasoning) → **local Ollama** via per-job `provider/model/base_url`
     override or a `no_agent` script. NEVER on Groq free — it 413s.
   - **Interactive** clarification/light work → Groq 70B + local `llama3.1:8b` fallback, kept
     under the 12k-token budget.
4. **Dispatch.** Use the framework's delegation orchestrator (`delegation.orchestrator_enabled`,
   `max_concurrent_children: 3`, `max_spawn_depth: 1`) or the heavy path. Build on it; don't rebuild.
   Prefer scripts over agent turns for long jobs.
5. **Review.** Run the result through the Review Board's three checklists
   (`standards/review-checklist.md`: Technical · Security · Integration).
6. **Approval gate.** Return a summary/diff for review. **Code merges and sends are approval-gated**
   — nothing ships without one-tap approval.

## Gates
- Routing must justify the chosen path. · Heavy work off Groq. · Merge/send require approval.

## Output
A reviewable spec + a routing decision (path/executor + why) + a diff/summary ready for the Review
Board and the approval gate.
