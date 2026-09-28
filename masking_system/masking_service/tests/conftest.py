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

from app.db.session import engine


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
