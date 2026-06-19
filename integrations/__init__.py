"""External-service integrations for Jack (Garmin, Google Calendar, Claude bridge).

Every external library used here is imported lazily inside methods so the test
suite and lint run without those libs installed, and every integration degrades
gracefully (returns None/'' and never raises into the caller) when its
credentials or service are unavailable.
"""
