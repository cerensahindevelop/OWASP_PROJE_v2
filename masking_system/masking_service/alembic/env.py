from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.db.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# render_as_batch=True: SQLite'in native ALTER TABLE'i cok sinirli (kolon/
# constraint DROP/ALTER'i dogrudan desteklemez) - bu bayrak, gelecekteki
# migration'larda op.drop_column/op.alter_column/op.create_foreign_key gibi
# islemleri Alembic'in "batch mode"una (tabloyu gecici olarak yeniden
# olusturup veriyi tasima) otomatik yonlendirir. Bu proje SQLite'a
# gecmeden ONCE (Postgres migration gecmisinde) bu bayrak yoktu - eski
# migration'lar squash edildigi icin sorun degil (bkz. 41a96747a9b4
# dokstring'i), ama BUNDAN SONRAKI her migration icin zorunludur.
def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
