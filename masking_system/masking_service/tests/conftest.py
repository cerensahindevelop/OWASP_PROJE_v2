"""Paylaşılan pytest fixture'ları.

`db_session`, teste izole, transaction-scoped bir Session verir: test edilen
kod ne yaparsa yapsın (kendi `db.flush()`/`db.commit()` çağrıları dahil),
teardown'da rollback edilir - bu suite'teki diğer testlerin (`SessionLocal()`
ile açılıp elle `DELETE` ile temizlenen) aksine, bu testler gerçek
veritabanında kalıcı hiçbir iz bırakmaz.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import engine


# Testler gelistiricinin .env'ine bagli olmamali: VLLM_ENABLED=true gercek
# model sunucusuna istek attirir, SCAN_GENERIC_COMPOUND_FILTER=true tespit
# sonuclarini degistirir. Her test kod varsayilanlariyla baslar; farkli
# deger isteyen test monkeypatch ile acikca ayarlar.
@pytest.fixture(autouse=True)
def _code_default_runtime_settings(monkeypatch):
    monkeypatch.setattr(settings.vllm, "enabled", False)
    monkeypatch.setattr(settings.scan, "generic_compound_filter", False)


@pytest.fixture()
def db_session():
    connection = engine.connect()
    outer_transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)

    # Test edilen kod session.commit() cagirirsa, bu normalde disaridaki
    # transaction'i da kapatir. Her seferinde yeni bir SAVEPOINT baslatarak
    # disaridaki transaction'i (ve teardown'daki rollback'i) commit()
    # cagrilsa bile canli tutariz.
    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, transaction):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()


# FastAPI TestClient; DB bagimliligi transaction-scoped `db_session`'a baglanir.
# raise_server_exceptions=False: hata yakalayicinin (app/api/errors.py) urettigi
# status/govde test edilir.
@pytest.fixture()
def api_client(db_session):
    from fastapi.testclient import TestClient

    from app.api.deps import get_request_db
    from app.api.main import app

    def _override():
        yield db_session

    app.dependency_overrides[get_request_db] = _override
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_request_db, None)
