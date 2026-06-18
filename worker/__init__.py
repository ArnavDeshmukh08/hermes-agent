"""Hermes Prime — event-driven lead-generation worker system.

Turns the Jack lead pipeline from a single script into an event-driven worker
system: Hermes Prime creates a task, spawns concurrent workers (Discovery,
Research, Social, Outreach, Validation), monitors a filesystem task queue,
aggregates results into a dynamic-column spreadsheet, and emits `task_complete`
so a Discord summary is sent automatically.

Design lineage: docs/LEAD-ENGINE.md (config-driven spec, provenance on every
row, mandatory validation, draft-only outreach). stdlib-only + lib.llm.
"""
