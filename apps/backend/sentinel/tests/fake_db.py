from __future__ import annotations

import asyncio
import copy
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from itertools import product

from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, False_, Null, True_
from sqlalchemy.sql.functions import FunctionElement
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

    def __iter__(self):
        return iter(self._rows)

    def scalars(self):
        return _FakeScalarResult(self._rows)

    def scalar_one(self):
        if len(self._rows) != 1:
            raise LookupError("Expected exactly one row")
        return self._rows[0]

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        return self._rows[0]


class FakeDB:
    """Minimal AsyncSession-like in-memory store for router tests."""

    def __init__(self):
        self.storage = defaultdict(list)
        self._tx_snapshots: list[dict] = []
        self._seed_auth_settings()

    def _seed_auth_settings(self) -> None:
        # Test-only convenience so login-based tests don't rely on app startup hooks.
        try:
            from app.services.auth_service import ensure_default_auth_settings
        except Exception:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(ensure_default_auth_settings(self))

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

    async def rollback(self):
        if self._tx_snapshots:
            snapshot = self._tx_snapshots[0]
            self.storage = defaultdict(list, copy.deepcopy(snapshot))
            self._tx_snapshots.clear()
        return None

    async def refresh(self, _obj):
        return None

    async def flush(self):
        return None

    class _TxContext:
        def __init__(self, db: "FakeDB"):
            self._db = db

        async def __aenter__(self):
            self._db._tx_snapshots.append(copy.deepcopy(dict(self._db.storage)))
            return self._db

        async def __aexit__(self, exc_type, _exc, _tb):
            snapshot = self._db._tx_snapshots.pop() if self._db._tx_snapshots else None
            if exc_type is not None and snapshot is not None:
                self._db.storage = defaultdict(list, copy.deepcopy(snapshot))
            return False

    def begin(self):
        return self._TxContext(self)

    async def get(self, model, obj_id):
        rows = self.storage.get(model, [])
        for row in rows:
            if getattr(row, "id", None) == obj_id:
                return row
        return None

    async def execute(self, stmt: Select):
        raw_columns = list(getattr(stmt, "_raw_columns", []))
        if len(raw_columns) == 1 and isinstance(raw_columns[0], FunctionElement):
            function_name = str(getattr(raw_columns[0], "name", "")).lower()
            if function_name == "count":
                from_obj = getattr(stmt, "_from_obj", ())
                if from_obj:
                    subquery = from_obj[0]
                    element = getattr(subquery, "element", None)
                    if isinstance(element, Select):
                        sub_result = await self.execute(element)
                        return _FakeResult([len(sub_result.scalars().all())])
                return _FakeResult([0])

        model = stmt.column_descriptions[0].get("entity")
        if model is None:
            return _FakeResult([])

        rows = list(self.storage.get(model, []))
        if not stmt._where_criteria:
            filtered = rows
        else:
            referenced_models = self._referenced_models(stmt, primary=model)
            filtered = [row for row in rows if self._row_matches(stmt, primary=model, row=row, related_models=referenced_models)]

        limit_clause = getattr(stmt, "_limit_clause", None)
        if limit_clause is not None:
            limit_value = getattr(limit_clause, "value", None)
            if isinstance(limit_value, int):
                filtered = filtered[:limit_value]
        return _FakeResult(filtered)

    def _referenced_models(self, stmt: Select, *, primary: type) -> list[type]:
        by_table = {
            getattr(model, "__tablename__", None): model
            for model in self.storage.keys()
            if getattr(model, "__tablename__", None)
        }
        models: list[type] = []
        for criterion in stmt._where_criteria:
            if not isinstance(criterion, BinaryExpression):
                continue
            for side in (criterion.left, criterion.right):
                table = getattr(side, "table", None)
                table_name = getattr(table, "name", None)
                candidate = by_table.get(table_name)
                if candidate is None or candidate is primary:
                    continue
                if candidate not in models:
                    models.append(candidate)
        return models

    def _row_matches(self, stmt: Select, *, primary: type, row, related_models: list[type]) -> bool:
        if not related_models:
            return all(self._evaluate({primary: row}, criterion) for criterion in stmt._where_criteria)

        related_rows = [list(self.storage.get(model, [])) for model in related_models]
        for combo in product(*related_rows):
            context = {primary: row}
            compatible = True
            for model, candidate in zip(related_models, combo, strict=True):
                if not self._rows_compatible(context, candidate):
                    compatible = False
                    break
                context[model] = candidate
            if not compatible:
                continue
            if all(self._evaluate(context, criterion) for criterion in stmt._where_criteria):
                return True
        return False

    @staticmethod
    def _rows_compatible(context: dict[type, object], candidate: object) -> bool:
        for existing in context.values():
            if hasattr(candidate, "session_id") and hasattr(existing, "id"):
                return getattr(candidate, "session_id") == getattr(existing, "id")
            if hasattr(existing, "session_id") and hasattr(candidate, "id"):
                return getattr(existing, "session_id") == getattr(candidate, "id")
        return True

    def _evaluate(self, context: dict[type, object], criterion) -> bool:
        if isinstance(criterion, BooleanClauseList):
            return all(self._evaluate(context, clause) for clause in criterion.clauses)

        if isinstance(criterion, BinaryExpression):
            actual = self._resolve_side(context, criterion.left)
            value = self._resolve_side(context, criterion.right)
            op = getattr(criterion.operator, "__name__", "")
            if op in {"eq", "is_"}:
                return actual == value
            if op in {"lt", "lt_op"}:
                return actual < value
            if op in {"gt", "gt_op"}:
                return actual > value
            if op in {"in_op"}:
                if value is None:
                    return False
                if isinstance(value, (list, tuple, set, frozenset)):
                    return actual in value
                return actual == value
            return False

        return False

    @staticmethod
    def _resolve_side(context: dict[type, object], side):
        if isinstance(side, True_):
            return True
        if isinstance(side, False_):
            return False
        if isinstance(side, Null):
            return None
        if hasattr(side, "value"):
            return getattr(side, "value")
        key = getattr(side, "key", None) or getattr(side, "name", None)
        table = getattr(side, "table", None)
        table_name = getattr(table, "name", None)
        if table_name:
            for model, row in context.items():
                if getattr(model, "__tablename__", None) == table_name and hasattr(row, key):
                    return getattr(row, key)
        for row in context.values():
            if key and hasattr(row, key):
                return getattr(row, key)
        return None
