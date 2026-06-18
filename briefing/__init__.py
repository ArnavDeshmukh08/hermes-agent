"""Jack's proactive morning-briefing package.

A standalone service (its own systemd unit) that, once a day at 09:00 IST,
compiles a short, personal briefing from Jack's living memory (USER.md) + the
day's reminders and delivers it to Discord via the shared reminder notifier.

Stdlib only — a simple next-fire time loop (no APScheduler), mirroring
reminders/scheduler.py so the deployment stays dependency-free.
"""
