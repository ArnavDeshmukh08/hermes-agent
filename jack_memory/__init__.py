"""Jack's living-memory package.

Named `jack_memory` (not `memory`) deliberately: `memory/` is gitignored
personal data locally and collides with the framework's `~/.hermes/memory/`
data directory on the VPS. This package holds CODE only:

- updater.py  — MemoryUpdater: extract new facts from a conversation (via Groq)
                and append them to the right USER.md section (never rewrites).
- schema.md   — documents the USER.md living-memory format + update rules.

The actual memory content lives in `~/.hermes/USER.md` (NOT in git — it holds
personal data). The seed for it is staged in the gitignored `secrets/`.
"""
