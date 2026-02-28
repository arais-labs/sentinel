from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.dependencies import get_db
from app.middleware.auth import require_permission
from app.database.models import Setting

router = APIRouter()


@router.get("/")
async def get_settings(db: Session = Depends(get_db), _=Depends(require_permission("settings.manage"))):
    rows = db.query(Setting).all()
    return {"settings": {r.key: r.value for r in rows}}


@router.put("/{key}")
async def set_setting(key: str, body: dict, db: Session = Depends(get_db), _=Depends(require_permission("settings.manage"))):
    value = body.get("value", "")
    if not value:
        raise HTTPException(status_code=400, detail="value is required")
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()
    return {"ok": True, "key": key, "value": value}
