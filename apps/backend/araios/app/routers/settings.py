from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.dependencies import get_db
from app.middleware.auth import require_permission
from app.database.models import SystemSetting

router = APIRouter()


@router.get("")
async def get_settings(db: Session = Depends(get_db), _=Depends(require_permission("settings.manage"))):
    rows = db.query(SystemSetting).all()
    return {"settings": {r.key: r.value for r in rows}}


@router.put("/{key}")
async def set_setting(key: str, body: dict, db: Session = Depends(get_db), _=Depends(require_permission("settings.manage"))):
    value = body.get("value", "")
    if not value:
        raise HTTPException(status_code=400, detail="value is required")
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=key, value=value))
    db.commit()
    return {"ok": True, "key": key, "value": value}
