"""Credential-free browser fixture for real query SSE and incremental Markdown.

Run with ``uv run --locked --extra dev python tests/web/stream_smoke_server.py``
and open http://127.0.0.1:8012/#chat. Every query uses a local streaming model
fixture; all database, snapshot and lock files live in a temporary directory.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

# Reuse the existing browser fixture's safe environment setup and seeded corpus.
from smoke_server import FixtureRetriever, seed_corpus

import uvicorn
from fastapi import Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from interview_intelligence.agent.query_contract import QuerySpec
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import CorpusState, SourceDocument, SourceRevision, create_database


ANSWER = """## Redis 为什么快？

Redis 主要通过**内存存储**和高效的数据结构减少访问开销。

1. 数据保存在内存中。
2. 事件循环使用 I/O 多路复用处理网络连接。
3. 常见操作使用适合访问模式的数据结构。

| 机制 | 作用 |
| --- | --- |
| 内存存储 | 减少磁盘访问 |
| I/O 多路复用 | 同时管理多个连接 |

```python
if latency < 3:
    print("响应很快")
```

> 实际性能仍取决于命令复杂度和数据大小。

原样展示 HTML：<img src=x onerror=alert(1)>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8012)
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="interview-stream-smoke-") as folder:
        root = Path(folder)
        (root / "corpus").mkdir()
        (root / "snapshots").mkdir()
        memory_db, _, _ = seed_corpus()
        database_path = root / "smoke.db"
        connection = memory_db.engine.raw_connection()
        with closing(sqlite3.connect(database_path)) as target:
            connection.driver_connection.backup(target)
        connection.close()
        memory_db.engine.dispose()
        database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
        db = create_database(database_url)
        with db.session() as session, session.begin():
            for revision, document in session.execute(select(SourceRevision, SourceDocument)
                    .join(SourceDocument, SourceRevision.source_document_id == SourceDocument.id)):
                raw = "# 流式浏览器验证\nRedis 为什么快？\n".encode()
                digest = hashlib.sha256(raw).hexdigest()
                revision.raw_file_hash = digest
                (root / "snapshots" / f"{digest}.md").write_bytes(raw)
                (root / "corpus" / document.original_relative_path).write_bytes(raw)
            session.get(CorpusState, 1).current_revision = 1
            session.get(CorpusState, 1).indexed_revision = 1
        settings = Settings(database_url=database_url, corpus_root=root / "corpus",
            snapshot_root=root / "snapshots", model_lock_path=root / "model.lock",
            local_user_id="isolated-stream-smoke", app_signing_key="isolated-browser-fixture",
            query_router_enabled=True, pi_agent_enabled=False, jev_decision_enabled=False,
            model_api_key="local-fixture", model_base_url=f"http://127.0.0.1:{options.port}/__fixture__/v1",
            query_model="local-stream-fixture", judge_model="local-stream-fixture",
            model_min_interval_seconds=0, elasticsearch_url=None)
        app = create_app(db, settings, FixtureRetriever())
        plan = QuerySpec(action="ANSWER", answer_text=ANSWER, answer_kind="EXPLAIN",
                         answer_basis="GENERAL_KNOWLEDGE").model_dump(mode="json")
        encoded_plan = json.dumps(plan, ensure_ascii=False)

        @app.post("/__fixture__/v1/chat/completions", include_in_schema=False)
        async def local_model(request: Request):
            payload = await request.json()
            assert payload["stream"] is True

            async def frames():
                # Split inside JSON escapes and Markdown delimiters as real providers do.
                for offset in range(0, len(encoded_plan), 12):
                    await asyncio.sleep(0.055)
                    chunk = {"choices": [{"index": 0,
                        "delta": {"content": encoded_plan[offset:offset + 12]}, "finish_reason": None}]}
                    yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
                yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 180, "total_tokens": 300}}) + "\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(frames(), media_type="text/event-stream")

        print(f"Isolated Markdown/SSE fixture: http://127.0.0.1:{options.port}/#chat", flush=True)
        try:
            uvicorn.run(app, host="127.0.0.1", port=options.port, log_level="warning")
        finally:
            db.engine.dispose()


if __name__ == "__main__":
    main()
