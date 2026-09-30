"""Keep test collection isolated from the user's live local configuration."""

import os


os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["ELASTICSEARCH_URL"] = ""
os.environ["MODEL_API_KEY"] = ""
os.environ["MODEL_BASE_URL"] = ""
