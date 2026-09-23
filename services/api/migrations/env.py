from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from bukmatika.config import get_settings
from bukmatika.persistence import (
    action_models,
    document_models,
    identity_models,
    library_organization_models,
    personalization_models,
    reader_models,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
_registered_models = (
    action_models.ActionExecutionReceipt,
    document_models.Document,
    identity_models.PrincipalSession,
    library_organization_models.LibraryCollection,
    personalization_models.UserModel,
    reader_models.ReadingState,
)
target_metadata = _registered_models[0].metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
