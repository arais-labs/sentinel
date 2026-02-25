from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList
from sqlalchemy.sql.selectable import Select


class _FakeScalarResult:
    def __init__(self, rows: list):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows: list):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        return self._rows[0]


class FakeDB:
    """Minimal AsyncSession-like in-memory store for router tests."""

    def __init__(self):
        self.storage = defaultdict(list)

    def add(self, obj):
        now = datetime.now(UTC)
        if hasattr(obj, "id") and getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if hasattr(obj, "created_at") and getattr(obj, "created_at", None) is None:
            obj.created_at = now
        if hasattr(obj, "updated_at") and getattr(obj, "updated_at", None) is None:
            obj.updated_at = now
        if hasattr(obj, "started_at") and getattr(obj, "started_at", None) is None:
            obj.started_at = now
        self.storage[type(obj)].append(obj)

    async def delete(self, obj):
        rows = self.storage[type(obj)]
        self.storage[type(obj)] = [row for row in rows if row is not obj]

    async def commit(self):
        return None

    async def refresh(self, _obj):
        return None

    async def execute(self, stmt: Select):
        model = stmt.column_descriptions[0].get("entity")
        if model is None:
            return _FakeResult([])

        rows = list(self.storage.get(model, []))
        for criterion in stmt._where_criteria:
            rows = [row for row in rows if self._evaluate(row, criterion)]
        return _FakeResult(rows)

    def _evaluate(self, row, criterion) -> bool:
        if isinstance(criterion, BooleanClauseList):
            return all(self._evaluate(row, clause) for clause in criterion.clauses)

        if isinstance(criterion, BinaryExpression):
            left = criterion.left
            right = criterion.right
            key = getattr(left, "key", None) or getattr(left, "name", None)
            value = getattr(right, "value", None)
            actual = getattr(row, key)
            op = getattr(criterion.operator, "__name__", "")
            if op in {"eq", "is_"}:
                return actual == value
            if op in {"lt", "lt_op"}:
                return actual < value
            if op in {"gt", "gt_op"}:
                return actual > value
            return False

        return False
