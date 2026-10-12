"""Database engine, session management and per-request tenant context.

Tenant identity is stored in PostgreSQL session variables (GUCs) that the
row-level security policies read. Values are set transaction-locally
(``set_config(..., true)``) so they never leak across pooled connections,
and they are ALWAYS derived from the authenticated token — never from a
client-supplied header or query parameter.
"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_settings = get_settings()
engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    future=True,
)
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one session/transaction per request."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def set_request_context(
    db: Session,
    *,
    user_id: str | None = None,
    org_id: str | None = None,
    bootstrap: bool = False,
) -> None:
    """Set transaction-local GUCs read by the RLS policies.

    ``user_id``/``org_id`` come from verified authentication state only.
    ``bootstrap`` is a one-shot flag used exclusively by the register
    endpoint to create its own organization row (guarded by an INSERT policy).
    """
    db.execute(
        text("SELECT set_config('app.current_user_id', :v, true)"),
        {"v": user_id or ""},
    )
    db.execute(
        text("SELECT set_config('app.current_org_id', :v, true)"),
        {"v": org_id or ""},
    )
    db.execute(
        text("SELECT set_config('app.is_bootstrap', :v, true)"),
        {"v": "true" if bootstrap else "false"},
    )
