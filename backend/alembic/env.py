import os
import sys
from logging.config import fileConfig

# Add the parent directory (the 'stonks' folder) to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alembic import context
from config import get_settings
from db.core import get_engine
from models.models import SQLModel

target_metadata = SQLModel.metadata

config = context.config
config.set_main_option("sqlalchemy.url", f"sqlite:///{get_settings().SQLITE_FILE}")
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        render_as_batch=True,
        literal_binds=True,
        transactional_ddl=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = get_engine()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            transactional_ddl=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
