FROM python:3.12.10-slim
WORKDIR /app
COPY pyproject.toml requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY config ./config
COPY prompts ./prompts
RUN pip install --no-cache-dir --no-deps .
CMD ["uvicorn", "interview_intelligence.api:app", "--host", "0.0.0.0", "--port", "8000"]
