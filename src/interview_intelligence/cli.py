"""Small CLI for the local API."""

from __future__ import annotations

import json

import httpx
import typer


app = typer.Typer(help="Interview Intelligence 本地命令")


def _call(method: str, path: str, *, api_url: str, params=None, body=None):
    if params is not None:
        params = {key: value for key, value in params.items() if value is not None}
    with httpx.Client(base_url=api_url.rstrip("/"), timeout=600, trust_env=False) as client:
        response = client.request(method, path, params=params, json=body)
    data = response.json()
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
    if response.is_error:
        raise typer.Exit(1)


@app.command()
def status(api_url: str = "http://127.0.0.1:8000"):
    """查看数据库、索引和模型配置状态。"""
    _call("GET", "/api/health", api_url=api_url)


@app.command()
def ingest(path: list[str] | None = None, mode: str = "changed",
           idempotency_key: str = typer.Option(...),
           api_url: str = "http://127.0.0.1:8000"):
    """将给定 Markdown 或完整语料目录加入后台导入队列。"""
    _call("POST", "/api/ingest", api_url=api_url,
          body={"paths": path or None, "mode": mode, "idempotency_key": idempotency_key})


@app.command("run")
def run_status(run_id: str, api_url: str = "http://127.0.0.1:8000"):
    """查看一次导入的进度和成本。"""
    _call("GET", f"/api/ingest/runs/{run_id}", api_url=api_url)


@app.command("retry-failed")
def retry_failed(run_id: str, idempotency_key: str = typer.Option(...),
                 api_url: str = "http://127.0.0.1:8000"):
    """仅将失败文件或失败的索引同步重新加入队列。"""
    _call("POST", f"/api/ingest/runs/{run_id}/retry-failed", api_url=api_url,
          body={"idempotency_key": idempotency_key})


@app.command()
def stats(company: str | None = None, topic_l1: str | None = None,
          round: str | None = None, group_by: str = "question",
          sort: str = "frequency", limit: int = 20,
          api_url: str = "http://127.0.0.1:8000"):
    """从完整语料计算频率和重要性。"""
    _call("GET", "/api/questions/stats", api_url=api_url,
          params={"company": company, "topic_l1": topic_l1, "round": round,
                  "group_by": group_by, "sort": sort, "limit": limit})


@app.command()
def search(query: str, company: str | None = None, pipeline: str = "HYBRID_RERANK",
           top_k: int = 10, api_url: str = "http://127.0.0.1:8000"):
    """查找语义相近的面试题。"""
    _call("GET", "/api/questions/search", api_url=api_url,
          params={"query": query, "company": company, "pipeline": pipeline, "top_k": top_k})


@app.command()
def detail(canonical_id: str, api_url: str = "http://127.0.0.1:8000"):
    """查看标准题及来源。"""
    _call("GET", f"/api/questions/{canonical_id}", api_url=api_url)


@app.command()
def occurrences(canonical_id: str, offset: int = 0, limit: int = 20,
                api_url: str = "http://127.0.0.1:8000"):
    """分页查看一道标准题的真实提问及来源。"""
    _call("GET", f"/api/questions/{canonical_id}/occurrences", api_url=api_url,
          params={"offset": offset, "limit": limit})


@app.command()
def source(revision_id: str, line_start: int | None = None, line_end: int | None = None,
           api_url: str = "http://127.0.0.1:8000"):
    """查看原始面经快照，可指定行范围。"""
    _call("GET", f"/api/sources/{revision_id}", api_url=api_url,
          params={"line_start": line_start, "line_end": line_end})


@app.command()
def chat(message: str, request_id: str | None = None,
         api_url: str = "http://127.0.0.1:8000"):
    """根据语料回答问题，显式复习指令可以记录个人状态。"""
    _call("POST", "/api/agent/chat", api_url=api_url,
          body={"message": message, "request_id": request_id})


@app.command()
def review(canonical_id: str, status: str, idempotency_key: str = typer.Option(...),
           score: int | None = None, note: str | None = None,
           api_url: str = "http://127.0.0.1:8000"):
    """记录一题的复习状态。"""
    _call("POST", "/api/review", api_url=api_url,
          body={"idempotency_key": idempotency_key, "items": [{
              "canonical_question_id": canonical_id, "status": status,
              "score": score, "note": note,
          }]})


if __name__ == "__main__":
    app()
