"""Jack's durable reminder system.

A standalone package (no framework dependency) so the scheduler can run as its
own systemd service, independent of the gateway:

- parser.py     — natural-language time parsing (IST input → UTC storage)
- store.py      — ReminderStore, JSON-backed, flock-safe (survives restarts)
- scheduler.py  — poll loop: fire due reminders, reschedule recurring ones
- notifier.py   — deliver to Discord via the bot REST API (no webhook needed)
"""
