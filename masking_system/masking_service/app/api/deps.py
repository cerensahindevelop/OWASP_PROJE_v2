"""FastAPI backend surecinin DB oturumu icin request-scoped dependency.

app/db/session.py::get_db()/session_scope() KASITLI OLARAK degistirilmedi -
CLI (app/cli.py) ve gelecekteki her cagiran taraf onlari oldugu gibi
kullanmaya devam eder. Burasi, sadece HTTP router'lari icin, commit/rollback
disiplinini TEK bir yerde toplayan AYRI (ek) bir dependency tanimlar.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from app.db.session import SessionLocal


# Her HTTP istegi icin bir DB session acar: handler basarili donerse commit,
# hata firlarsa rollback eder - router'larin kendi commit() cagirmasina gerek yok.
def get_request_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
