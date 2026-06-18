# Agent & Skill Authoring

> How to write a Hermes agent (`.claude/agents/`) or skill (`.claude/skills/`) file.
> Canonical examples: `agents/architect.md`, `agents/dispatcher.md`,
> `skills/context-compressor.md`, `skills/deployment-validator.md`.

## Canonical format
Every agent/skill file follows the same shape (keep it scannable, ~30–45 lines):

```
# Agent: <Name>   (or)   # Skill: <Name>
**Responsibility:** one or two sentences — what it owns AND what it does NOT
own (point to the sibling that does).
## Use when / Operating context   (the real Hermes facts it must internalize)
## Method / Workflow              (numbered steps)
## Guardrails / Don't             (optional heading; gates/traps may also be stated inline)
## Output                         (the concrete artifact it returns)
```

- Open with `**Responsibility:**` and end with `## Output`. These are mandatory; the
  Guardrails/Don't section is recommended but may be folded inline (as the diagnostic
  agents do) — what matters is that boundaries and gates are explicit somewhere.
- State boundaries in the Responsibility line ("not live ops — see `infrastructure`").

## Single responsibility / no overlap
- Each agent/skill owns ONE area. If two files could both handle a task, the boundary is wrong.
- Reference the sibling that owns adjacent work by **relative path** (`agents/infrastructure.md`,
  `skills/log-analyzer.md`), and hand work to it rather than duplicating its logic.
- Current ownership map (don't re-implement): design→`architect`, live ops→`infrastructure`,
  debugging→`debugger`, memory→`memory-manager`, dispatch→`dispatcher`, outreach→`outreach-manager`;
  context→`context-compressor`, deploys→`deployment-validator`, health→`hermes-doctor`,
  logs→`log-analyzer`, provider→`provider-debugger`, telegram→`telegram-debugger`, bugs→`bug-fixer`.

## Status honesty
- Tell the agent/skill to mark results done / partial / blocked / needs-evidence.
- Never instruct it to claim success it can't verify. "Partial spec > fabricated result."

## Adapt to Hermes's real surfaces
- Write against the ACTUAL system: Telegram gateway, `hermes-agent` v0.16.0,
  `systemd --user hermes-gateway.service`, cwd `/home/hermes/.hermes`, Groq 70B + Ollama
  `llama3.1:8b` fallback, the three-path model, the 12k-TPM budget.
- Cite real paths (`docs/AUDIT.md`, `~/.hermes/config.yaml`, `~/.hermes/logs/agent.log`).
- NO cargo-culted generic-software language ("leverage synergies", invented microservices,
  features Hermes doesn't have). If a role doesn't map to a real Hermes surface, don't write it.

## Authoring checklist
- [ ] `# Agent:`/`# Skill:` title + `**Responsibility:**` with explicit boundary
- [ ] Single responsibility; no overlap with a sibling; siblings referenced by relative path
- [ ] Operating context uses real Hermes facts, not generic placeholders
- [ ] Respects the standards: context budget, three-path routing, approval gates (`standards/engineering-standards.md`)
- [ ] Status-honesty instruction present
- [ ] Ends with a concrete `## Output` artifact
- [ ] File stays small and scannable
