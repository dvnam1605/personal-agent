import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.infrastructure.db.models  # noqa: F401
from alembic import context
from app.core.config import settings
from app.infrastructure.db.base import Base

config = context.config

# Programmatic callers (tests) may pass attributes["configure_logger"] = False
# so fileConfig cannot disable already-imported loggers for the whole session.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Runtime-generated columns live only in migrations (migration 0007), never in
# the ORM models: SQLite unit-runtime cannot evaluate to_tsvector() on INSERT.
# Autogenerate compares DB -> metadata and would therefore emit a spurious
# drop_column for them; filter those columns out of every diff instead.
_GENERATED_FILTERED_COLUMNS = {"document_chunks": {"search_vector"}}


def _include_object(obj, name, type_, reflected, compare_to) -> bool:  # noqa: ANN001
    """Keep generated tsvector columns out of autogenerate diffs."""
    table = getattr(obj, "table", None)
    table_name = getattr(table, "name", None) if table is not None else None
    if type_ == "column" and isinstance(table_name, str):
        if name in _GENERATED_FILTERED_COLUMNS.get(table_name, set()):
            return False
    return True


def migration_url() -> str:
    """Use an explicit Alembic URL when supplied, otherwise application settings."""
    return config.get_main_option("sqlalchemy.url") or settings.database.url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = migration_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_object=_include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
        version_table_schema=config.get_main_option("version_table_schema") or None,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine and associate a connection with the context."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = migration_url()
    connect_args = config.attributes.get("connect_args", {})

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
