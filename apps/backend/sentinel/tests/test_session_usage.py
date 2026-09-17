import asyncio
from decimal import Decimal

from app.models import Message
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.compaction import CompactionService
from app.services.sessions.service import SessionService
from app.services.sessions.usage import session_usage
from tests.compaction_fixtures import HandoffProvider
from tests.fake_db import FakeDB
from tests.test_runtime_support import _new_session


def snapshot(kind="api_list_price", price="0.00014"):
    return {
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "price_kind": kind,
        "price": {"usd": price} if price is not None else None,
    }


def test_separate_subscription_equivalent_and_api_cost_without_float_rounding():
    total = session_usage(
        [snapshot(), snapshot(), snapshot("api_equivalent"), snapshot(price=None), None]
    )
    assert total["requests"] == 5
    assert total["unreported_requests"] == 1
    assert total["costs"]["api_list_price"] == {
        "usd": "0.00028",
        "priced_requests": 2,
        "unpriced_requests": 1,
    }
    assert total["costs"]["api_equivalent"]["usd"] == "0.00014"


def test_session_total_covers_all_pages_and_survives_repeated_compaction():
    async def check():
        db = FakeDB()
        session = _new_session(db)
        service = SessionService(run_registry=AgentRunRegistry())
        compactor = CompactionService(HandoffProvider())

        def add_turns(count):
            for _ in range(count):
                db.add(
                    Message(
                        session_id=session.id, role="user", content="hello " * 30, metadata_json={}
                    )
                )
                db.add(
                    Message(
                        session_id=session.id,
                        role="assistant",
                        content="reply " * 30,
                        metadata_json={"provider_usage": snapshot()},
                    )
                )

        async def total():
            return await service.get_usage(db, session_id=session.id, user_id=session.user_id)

        add_turns(60)
        before = await total()
        assert before["requests"] == 60
        await compactor._compact(db, session)
        conversation = [
            m for m in db.storage[Message] if (m.metadata_json or {}).get("purpose") != "compaction"
        ]
        assert len(conversation) == 120
        assert await total() == before
        add_turns(20)
        before_second = await total()
        await compactor._compact(db, session)
        assert await total() == before_second
        assert before_second["requests"] == 80
        assert Decimal(before_second["costs"]["api_list_price"]["usd"]) == Decimal(".0112")
        assert not before_second["history_incomplete"]

    asyncio.run(check())
