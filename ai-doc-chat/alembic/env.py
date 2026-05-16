import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
from rag_app.models import Base  # your models

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

db_url = os.environ["DATABASE_URL"].replace(
    "postgresql+asyncpg", "postgresql+psycopg2"
)
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata