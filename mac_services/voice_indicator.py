#!/usr/bin/env python3
"""Jack Voice Indicator — macOS menu bar status item.

Shows the current state of the Jack voice service via a menu bar icon.
Polls /tmp/jack-voice-state every 300 ms.

States and icons:
  idle      → ◦   (hollow dot, gray)
  recording → ◉   (filled circle, active)
  thinking  → ◎   (double circle, processing)
  speaking  → ●   (solid dot, outputting)

Requires PyObjC (ships with macOS Python or: pip install pyobjc-framework-Cocoa).
Run as a standalone process — it owns its own NSRunLoop.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time

# ---------------------------------------------------------------------------
# AppKit import — fail loudly if PyObjC is not available
# ---------------------------------------------------------------------------

try:
    import AppKit
    import objc
    from Foundation import NSObject, NSTimer, NSRunLoop, NSDefaultRunLoopMode, NSDate
except ImportError:
    print(
        "ERROR: PyObjC not found. Install with:\n"
        "  pip install pyobjc-framework-Cocoa",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_FILE = "/tmp/jack-voice-state"

# Menu bar title for each state.  Pure Unicode — no image generation needed.
ICONS: dict[str, str] = {
    "idle":      "◦",
    "recording": "◉",
    "thinking":  "◎",
    "speaking":  "●",
}

POLL_INTERVAL = 0.3  # seconds


# ---------------------------------------------------------------------------
# AppDelegate — owns the status item and the poll timer
# ---------------------------------------------------------------------------

class JackIndicatorDelegate(NSObject):
    """NSApplication delegate that manages the menu bar status item."""

    def applicationDidFinishLaunching_(self, notification: objc.objc_object) -> None:
        # ── Status item ──────────────────────────────────────────────────────
        bar = AppKit.NSStatusBar.systemStatusBar()
        self._status_item = bar.statusItemWithLength_(
            AppKit.NSVariableStatusItemLength
        )

        btn = self._status_item.button()
        btn.setTitle_(ICONS["idle"])
        btn.setFont_(
            AppKit.NSFont.menuBarFontOfSize_(14)
        )

        # ── Menu ─────────────────────────────────────────────────────────────
        menu = AppKit.NSMenu.alloc().init()

        # State label (disabled — informational only)
        self._state_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "State: idle", None, ""
        )
        self._state_item.setEnabled_(False)
        menu.addItem_(self._state_item)

        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit Jack Indicator", "terminate:", "q"
        )
        menu.addItem_(quit_item)

        self._status_item.setMenu_(menu)

        # ── Poll timer (fires on the main run loop) ───────────────────────────
        self._last_state: str = "idle"
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            POLL_INTERVAL,
            self,
            "pollStateFile:",
            None,
            True,
        )

    def pollStateFile_(self, timer: objc.objc_object) -> None:
        """Read the state file and update the menu bar icon if state changed."""
        try:
            with open(STATE_FILE) as fh:
                raw = fh.read().strip().lower()
            state = raw if raw in ICONS else "idle"
        except (FileNotFoundError, OSError):
            state = "idle"

        if state == self._last_state:
            return

        self._last_state = state
        icon = ICONS[state]

        btn = self._status_item.button()
        btn.setTitle_(icon)

        self._state_item.setTitle_(f"State: {state}")

    def applicationShouldTerminate_(self, sender: objc.objc_object) -> int:
        return AppKit.NSTerminateNow


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Create the shared NSApplication instance.
    app = AppKit.NSApplication.sharedApplication()

    # Accessory policy: no Dock icon, no menu-bar app switcher entry.
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    # Wire up the delegate.
    delegate = JackIndicatorDelegate.alloc().init()
    app.setDelegate_(delegate)

    # Handle SIGTERM / SIGINT gracefully (launchctl stop sends SIGTERM).
    def _quit(signum: int, frame: object) -> None:
        AppKit.NSApplication.sharedApplication().terminate_(None)

    signal.signal(signal.SIGTERM, _quit)
    signal.signal(signal.SIGINT, _quit)

    # Run the application (blocks until terminate_ is called).
    app.run()


if __name__ == "__main__":
    main()
