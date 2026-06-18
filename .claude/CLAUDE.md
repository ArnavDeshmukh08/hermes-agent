# Hermes Development Operating System

Mission: Build a reliable Telegram-first personal assistant for Arnav.

Rules:
- Fix root causes, never patch symptoms.
- Every bug starts with logs.
- Any change must include validation steps.
- Keep context files under 5k lines.
- Archive old conversations and memories.
- Prefer scripts over agent turns for long-running jobs.
- Approval required for sending messages, spending money, deployments.

Workflow:
1. Reproduce
2. Gather logs
3. Identify subsystem
4. Create hypothesis
5. Test
6. Fix
7. Validate
8. Update CONTEXT.md and MEMORY.md
