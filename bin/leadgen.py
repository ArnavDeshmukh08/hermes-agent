#!/usr/bin/env python3
"""Hermes Prime lead-engine CLI.

    bin/leadgen.py --spec spec.json            # parallel worker run (default)
    bin/leadgen.py --spec -                     # read spec JSON from stdin
    bin/leadgen.py --spec spec.json --mode sequential
    bin/leadgen.py --spec spec.json --mode compare   # sequential vs parallel timing

The spec is dynamic — new lead tasks need only a different target/location/columns:

    {"target": "psychiatry clinics", "location": "India",
     "columns": ["name", "clinic", "phone", "email", "website"], "outreach": true}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.prime import HermesPrime  # noqa: E402
from worker.specs import SpecError, load_spec_json  # noqa: E402


def _read_spec(arg: str) -> str:
    if arg == "-":
        return sys.stdin.read()
    return Path(arg).read_text(encoding="utf-8")


async def _amain(args) -> int:
    try:
        spec = load_spec_json(_read_spec(args.spec))
    except SpecError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2

    prime = HermesPrime(notify=not args.no_notify)

    if args.mode == "compare":
        res = await prime.compare(spec)
        print(json.dumps(
            {
                "sequential_s": round(res["sequential_s"], 3),
                "parallel_s": round(res["parallel_s"], 3),
                "speedup": round(res["speedup"], 2),
                "rows": res["parallel_written"],
            },
            indent=2,
        ))
        return 0

    if args.mode == "sequential":
        res = await prime.run_sequential(spec)
    else:
        res = await prime.run_parallel(spec)

    summary = {k: v for k, v in res.items() if k != "leads"}
    summary["runtime_s"] = round(summary.get("runtime_s", 0), 3)
    print(json.dumps(summary, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Hermes Prime lead-engine worker system")
    p.add_argument("--spec", required=True, help="path to spec JSON, or '-' for stdin")
    p.add_argument("--mode", choices=["parallel", "sequential", "compare"], default="parallel")
    p.add_argument("--no-notify", action="store_true", help="suppress the Discord notification")
    args = p.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
