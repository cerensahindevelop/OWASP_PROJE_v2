from contextlib import contextmanager

# DB engine'i (create_engine) ve session factory'sini (sessionmaker)
# olusturmak icin kullanilan cekirdek SQLAlchemy bilesenleri. event: her yeni
# ham DBAPI baglantisinda foreign_keys pragma'sini acmak icin.
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

# check_same_thread=False: sqlite3'un varsayilani, bir baglantinin sadece
# olusturuldugu thread'den kullanilmasini zorunlu kilar - Streamlit'in
# istek basina farkli thread kullanmasi ve SessionLocal()'in cagrildigi
# thread'in engine'i olusturan thread'den FARKLI olmasi (havuzdaki
# baglanti farkli bir thread'e verilebilir) yuzunden bu kisitlamayi
# kapatmak gerekiyor. Guvenli: bu uygulamada bir SQLAlchemy Session (ve
# dolayisiyla altindaki baglanti) asla iki thread arasinda PAYLASILMAZ -
# her request/CLI cagrisi kendi session_scope()'unu acip kapatir.
engine = create_engine(settings.database_url, future=True, connect_args={"check_same_thread": False})


# Her yeni DB baglantisinda: foreign key kontrolünü açar, WAL moduna
# geçer ve kilit doluysa 30 saniyeye kadar bekler (eşzamanlı export/unmask
# isteklerinde ham "database is locked" hatası yerine bekleyip devam eder).
@event.listens_for(engine, "connect")
def _configure_sqlite_connection(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


# Yeni DB session'lari uretmek icin kullanilan factory.
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


# Bir DB session acip is bittiginde (basarili ya da hatali) kapatilmasini
# garanti eden generator - session_scope() bunu contextmanager ile sarar.
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# `with session_scope() as db: ...` - session acma/kapama mantigi tek
# yerde tanimli, cagri noktalarinda tekrarlanmaz (bkz. app/cli.py,
# app/webapp/common.py).
session_scope = contextmanager(get_db)
