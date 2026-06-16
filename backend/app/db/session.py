"""DB 엔진/세션 팩토리 — config.database_url 기반(기본 SQLite).

spec: specs/reporter_agent.md

ADR-004: 개발 DB는 SQLite, 프로덕션은 PostgreSQL. SQLAlchemy로 추상화해 전환 비용을 낮춘다.
I/O는 이 레이어(+에이전트)에만 둔다 — 집계는 순수 함수(`agents/reporter.py`).
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import Settings
from backend.app.db.models import Base


def make_engine(database_url: str | None = None) -> Engine:
    """엔진을 생성한다. database_url 미지정 시 설정값(기본 SQLite)을 사용한다.

    SQLite는 멀티스레드 접근을 위해 check_same_thread=False. 인메모리(:memory:)는
    StaticPool로 단일 연결을 공유해 테스트 간 같은 DB를 유지한다(파일 DB 오염 방지).
    """
    url = database_url or Settings().database_url
    connect_args: dict = {}
    kwargs: dict = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
    return create_engine(url, connect_args=connect_args, **kwargs)


def make_session_factory(
    database_url: str | None = None, *, create: bool = True
) -> Callable[[], Session]:
    """세션 팩토리를 만든다. create=True면 테이블을 생성한다.

    expire_on_commit=False — 커밋 후 반환된 ORM 객체의 속성 접근을 안전하게 한다.
    """
    engine = make_engine(database_url)
    if create:
        Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
