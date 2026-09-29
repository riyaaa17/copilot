from collections.abc import Iterator
from sqlmodel import Session, SQLModel, create_engine
from app.config import get_settings

_settings = get_settings()
_args = {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
engine = create_engine(_settings.database_url, connect_args=_args)


def init_db() -> None:
    from app.models import tables  # noqa: F401  (registers the tables)
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session