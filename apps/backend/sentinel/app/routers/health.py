from datetime import UTC, datetime

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/v1/health")
async def versioned_health() -> dict[str, str]:
    return {
        "status": "ok",
        "version": "0.1.0",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/health/ready")
async def ready() -> dict[str, str]:
    # DB and dependency checks are added in later phases.
    return {"status": "ready"}
