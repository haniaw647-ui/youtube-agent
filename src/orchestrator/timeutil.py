from datetime import UTC, datetime


def utcnow_naive() -> datetime:
    """Every timestamp column in this schema is `timestamp without time zone`
    — asyncpg rejects a tz-aware Python datetime bound against one (raises
    "can't subtract offset-naive and offset-aware datetimes"), on both INSERT
    and comparison. Always build "now" for these columns through this helper
    rather than datetime.now(UTC) directly; the value itself is still UTC,
    just stripped of tzinfo before it reaches the driver."""
    return datetime.now(UTC).replace(tzinfo=None)
