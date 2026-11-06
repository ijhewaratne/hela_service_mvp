"""Alembic environment. Migrations run as the owner role (HELA_MIGRATION_DATABASE_URL), never as hela_app."""
import os

from alembic import context
from sqlalchemy import create_engine

DEFAULT_URL = 'postgresql+psycopg://hela_owner:hela_owner_dev_only@127.0.0.1:55432/hela'


def run_migrations_online():
    url = os.environ.get('HELA_MIGRATION_DATABASE_URL', DEFAULT_URL)
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit('Offline SQL generation is not supported; run against a database.')
run_migrations_online()
