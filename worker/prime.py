"""Hermes Prime — the orchestrator (mission §"Architecture").

    creates task -> spawns workers -> monitors status -> aggregates results
                 -> emits task_complete -> Discord summary sent automatically

Two execution paths share the same workers so the runtime comparison is honest:
- run_parallel: queue-driven worker pool; discovery fans out across sources,
  every lead is enriched concurrently, and B(research)∥C(social) run together.
- run_sequential: the same stages strictly one-at-a-time (the baseline).

Concurrency uses asyncio (every IO/LLM call is a coroutine; real calls run in a
thread, mock calls await a simulated latency).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import traceback

from . import discord, leadgen
from .events import TASK_COMPLETE, EventBus
from .queue import Task, TaskQueue, new_task_id
from .sheets import SheetWriter
from .specs import LeadSpec, role_map
from .workers import WORKERS

DEFAULT_CONCURRENCY = int(os.environ.get("HERMES_CONCURRENCY", "8"))


def _take_new(seen: set, discovered: list, leads: list, limit: int) -> list:
    """Dedupe `leads` (phone>email>name key) against `seen`, cap at `limit`,
    record into `seen`/`discovered`, and return the freshly-accepted leads.
    Shared by the parallel and sequential paths so the logic lives in one place."""
    fresh = []
    for lead in leads:
        if len(seen) >= limit:
            break
        key = leadgen._dedupe_key(lead)
        if key in seen:
            continue
        seen.add(key)
        discovered.append(lead)
        fresh.append(lead)
    return fresh


class HermesPrime:
    def __init__(self, queue: TaskQueue | None = None, bus: EventBus | None = None, notify: bool = True):
        self.queue = queue or TaskQueue()
        self.bus = bus or EventBus()
        if notify:
            self.bus.subscribe(TASK_COMPLETE, discord.send_summary)

    # -- shared per-lead pipeline: B∥C -> D -> E ----------------------------
    async def enrich_lead(self, lead: dict, spec: LeadSpec, *, concurrent: bool) -> dict:
        lead = dict(lead)
        if concurrent:
            research, social = await asyncio.gather(
                WORKERS["research"].run(lead, spec),
                WORKERS["social"].run(lead, spec),
            )
        else:
            research = await WORKERS["research"].run(lead, spec)
            social = await WORKERS["social"].run(lead, spec)
        lead.update({k: v for k, v in research.items() if v is not None})
        lead.update({k: v for k, v in social.items() if v is not None})
        if spec.outreach:
            out = await WORKERS["outreach"].run(lead, spec)
            if out.get("pitch"):
                # route into the user's pitch-role column if they named one, else a default key
                pitch_label = role_map(spec.columns).get("pitch", "pitch")
                lead[pitch_label] = out["pitch"]
        verdict = await WORKERS["validate"].run(lead, spec)
        lead["_valid"] = verdict["valid"]
        lead["_validation"] = verdict["reasons"]
        return lead

    # -- aggregation + notification (shared) --------------------------------
    def _write_and_summarize(self, spec, discovered, enriched, runtime_s, mode, failed=0) -> dict:
        valid = [lead for lead in enriched if lead.get("_valid")]
        writer = SheetWriter(spec.tab, spec.columns, spec.outreach)
        manifest = writer.write(valid)

        pitch_label = role_map(spec.columns).get("pitch", "pitch")
        with_prov = sum(1 for lead in valid if lead.get("source_url"))
        pitches = sum(1 for lead in valid if lead.get(pitch_label))
        return {
            "spec": {
                "target": spec.target,
                "location": spec.location,
                "sources": list(spec.sources),
                "count": spec.count,
            },
            "columns": list(spec.columns),
            "outreach": spec.outreach,
            "counts": {
                "discovered": len(discovered),
                "validated": len(valid),
                "written": manifest["rows"],
                "with_provenance": with_prov,
                "pitches": pitches,
            },
            "sheet": manifest,
            "runtime_s": runtime_s,
            "mode": mode,
            "failed": failed,
            "leads": valid,
        }

    # -- PARALLEL: queue-driven worker pool ---------------------------------
    async def run_parallel(self, spec: LeadSpec, concurrency: int = DEFAULT_CONCURRENCY) -> dict:
        self.queue.drain()
        t0 = time.monotonic()
        top = self.queue.open_task(Task(id=new_task_id("task"), kind="task", payload={"target": spec.target}))

        discovered: list[dict] = []
        enriched: list[dict] = []
        seen: set[str] = set()
        seen_lock = asyncio.Lock()
        outstanding = 0
        done = asyncio.Event()

        def _bump(n):
            nonlocal outstanding
            outstanding += n
            if outstanding == 0:
                done.set()

        # seed one discovery task per source
        for source in spec.sources:
            self.queue.enqueue(Task(id=new_task_id("discover"), kind="discover", payload={"source": source}, parent=top.id))
            _bump(1)

        async def worker_loop():
            while not done.is_set():
                task = self.queue.claim()
                if task is None:
                    await asyncio.sleep(0.005)
                    continue
                try:
                    if task.kind == "discover":
                        res = await WORKERS["discover"].run(task.payload, spec)
                        async with seen_lock:
                            fresh = _take_new(seen, discovered, res["leads"], spec.count)
                        children = [
                            Task(id=new_task_id("enrich"), kind="enrich", payload={"lead": lead}, parent=top.id)
                            for lead in fresh
                        ]
                        # enqueue children BEFORE completing parent (closes the termination race)
                        for child in children:
                            self.queue.enqueue(child)
                            _bump(1)
                        self.queue.complete(task, {"source": res["source"], "found": len(res["leads"])})
                        _bump(-1)
                    elif task.kind == "enrich":
                        lead = await self.enrich_lead(task.payload["lead"], spec, concurrent=True)
                        self.queue.complete(task, {"valid": lead.get("_valid"), "lead": lead})
                        if lead.get("_valid"):
                            enriched.append(lead)
                        _bump(-1)
                except Exception as e:  # noqa: BLE001 - one bad task must not sink the pool
                    traceback.print_exc(file=sys.stderr)  # surface it, never silent
                    self.queue.fail(task, repr(e))
                    _bump(-1)

        pool = [asyncio.create_task(worker_loop()) for _ in range(max(1, concurrency))]
        await done.wait()
        for p in pool:
            p.cancel()
        await asyncio.gather(*pool, return_exceptions=True)

        runtime = time.monotonic() - t0
        result = self._write_and_summarize(
            spec, discovered, enriched, runtime, "parallel", failed=self.queue.counts()["failed"]
        )
        self.queue.complete(top, {k: v for k, v in result.items() if k != "leads"})
        await self.bus.emit(TASK_COMPLETE, result)
        return result

    # -- SEQUENTIAL baseline (no pool, no internal concurrency) --------------
    async def run_sequential(self, spec: LeadSpec) -> dict:
        t0 = time.monotonic()
        discovered: list[dict] = []
        seen: set[str] = set()
        for source in spec.sources:  # one source at a time
            res = await WORKERS["discover"].run({"source": source}, spec)
            _take_new(seen, discovered, res["leads"], spec.count)

        enriched = []
        for lead in discovered:  # one lead at a time, B then C (no gather)
            enriched.append(await self.enrich_lead(lead, spec, concurrent=False))

        runtime = time.monotonic() - t0
        return self._write_and_summarize(spec, discovered, enriched, runtime, "sequential")

    async def compare(self, spec: LeadSpec, concurrency: int = DEFAULT_CONCURRENCY) -> dict:
        """Run sequential (silent) then parallel (notifies); return both runtimes."""
        seq = await self.run_sequential(spec)
        par = await self.run_parallel(spec, concurrency=concurrency)
        speedup = (seq["runtime_s"] / par["runtime_s"]) if par["runtime_s"] else float("inf")
        return {
            "sequential_s": seq["runtime_s"],
            "parallel_s": par["runtime_s"],
            "speedup": speedup,
            "sequential_written": seq["counts"]["written"],
            "parallel_written": par["counts"]["written"],
            "parallel_result": par,
        }
