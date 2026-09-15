"""Session accounting from persisted provider snapshots, independent of pagination."""

from decimal import Decimal

from sqlalchemy import select

from app.models import Message


def session_usage(snapshots):
    totals = {
        "requests": 0,
        "unreported_requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "costs": {},
        "history_incomplete": False,
    }
    for snapshot in snapshots:
        totals["requests"] += 1
        usage = (snapshot or {}).get("usage") or {}
        if not all(type(usage.get(key)) is int for key in ("input_tokens", "output_tokens")):
            totals["unreported_requests"] += 1
            continue
        for key in ("input_tokens", "output_tokens"):
            totals[key] += usage[key]
        kind = snapshot["price_kind"]
        cost = totals["costs"].setdefault(
            kind, {"usd": "0", "priced_requests": 0, "unpriced_requests": 0}
        )
        price = snapshot.get("price")
        if price is None:
            cost["unpriced_requests"] += 1
        else:
            cost["usd"] = str(Decimal(cost["usd"]) + Decimal(price["usd"]))
            cost["priced_requests"] += 1
    return totals


async def conversation_usage(db, session_id):

    rows = (
        (await db.execute(select(Message).where(Message.session_id == session_id))).scalars().all()
    )
    return session_usage(
        [
            (row.metadata_json or {}).get("provider_usage")
            for row in rows
            if not (row.metadata_json or {}).get("forked_from_message_id")
            and (row.role == "assistant" or (row.metadata_json or {}).get("source") == "usage")
        ]
    )


def merge_usage(groups):
    totals = session_usage([])
    for group in groups:
        for field in ("requests", "unreported_requests", "input_tokens", "output_tokens"):
            totals[field] += group[field]
        totals["history_incomplete"] |= group["history_incomplete"]
        for kind, value in group["costs"].items():
            cost = totals["costs"].setdefault(
                kind, {"usd": "0", "priced_requests": 0, "unpriced_requests": 0}
            )
            cost["usd"] = str(Decimal(cost["usd"]) + Decimal(value["usd"]))
            cost["priced_requests"] += value["priced_requests"]
            cost["unpriced_requests"] += value["unpriced_requests"]
    return totals
