#!/usr/bin/env python3
"""Founder brain-dump CLI (Phase 6) — the entrypoints a `no_agent` cron invokes.

  python bin/brain.py --capture '<json>'   # record one raw #brain-dump message (testing/relay)
  python bin/brain.py --parse              # nightly: classify+extract new entries -> knowledge/
  python bin/brain.py --report             # morning: print the digest (cron relays to Discord)

No new infra: these are plain scripts. The live Discord bot calls knowledge.record_braindump
directly from its #brain-dump on_message handler (see bin/discord_bot.py).
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import knowledge  # noqa: E402


def _arg(argv, flag):
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--capture" in argv:
        raw = _arg(argv, "--capture")
        if raw is None and not sys.stdin.isatty():
            raw = sys.stdin.read().strip() or None
        if not raw:
            print(json.dumps({"ok": False, "reason": "no_input"}))
            return 1
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            print(json.dumps({"ok": False, "reason": "bad_json"}))
            return 1
        entry = knowledge.record_braindump(
            msg.get("message_id"), msg.get("text"),
            author=msg.get("author", "founder"), ts=msg.get("ts"))
        print(json.dumps({"ok": entry is not None,
                          "captured": bool(entry),
                          "message_id": msg.get("message_id")}, ensure_ascii=False))
        return 0

    if "--parse" in argv:
        summary = knowledge.parse_new()
        summary.pop("items", None)
        print(json.dumps({"ok": True, **summary}, ensure_ascii=False))
        return 0

    if "--report" in argv:
        print(knowledge.morning_report())
        return 0

    print(json.dumps({"ok": False, "reason": "no_command",
                      "usage": "--capture '<json>' | --parse | --report"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
