"""Print a live project status snapshot from the configured database."""

import json

from interview_intelligence.config import load_settings
from interview_intelligence.domain.models import create_database
from interview_intelligence.reports.status import corpus_status


if __name__ == "__main__":
    settings = load_settings()
    database = create_database(settings.database_url)
    print(json.dumps(corpus_status(database), ensure_ascii=False, indent=2))
