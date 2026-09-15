import math
import struct
from datetime import UTC, datetime

from sqlalchemy import DateTime, LargeBinary
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC timestamps and restore timezone information omitted by SQLite."""

    impl = DateTime
    cache_ok = True

    @property
    def python_type(self):
        return datetime

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        return value.replace(tzinfo=UTC) if value is not None else None


class EmbeddingVector(TypeDecorator[list[float]]):
    """Compact float32 vectors understood directly by sqlite-vec."""

    impl = LargeBinary
    cache_ok = True

    @property
    def python_type(self):
        return list

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not value or not all(math.isfinite(x) for x in value):
            raise ValueError("Embeddings must contain finite numbers")
        return struct.pack(f"<{len(value)}f", *value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return list(struct.unpack(f"<{len(value) // 4}f", value))
