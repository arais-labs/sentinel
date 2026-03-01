from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from config import DATABASE_URL


class Base(DeclarativeBase):
    pass


engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    from app.database import models  # noqa: F401 — ensure all models registered
    Base.metadata.create_all(bind=engine)

    # Seed default permissions — insert any missing actions, never overwrite existing
    from app.permissions import AGENT_PERMISSIONS
    session = SessionLocal()
    try:
        existing = {p.action for p in session.query(models.Permission).all()}
        for action, level in AGENT_PERMISSIONS.items():
            if action not in existing:
                session.add(models.Permission(action=action, level=level))
        session.commit()
    finally:
        session.close()
