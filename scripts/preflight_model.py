"""Small live capability check; never prints a credential or full response."""

import json
from pathlib import Path

from interview_intelligence.config import (
    load_settings, validate_backend_model_endpoint, validate_model_preflight,
)
from interview_intelligence.dedup.provider import ArkMultimodalEncoder, OpenAICompatibleEncoder, OpenAICompatibleJudge
from interview_intelligence.extraction.provider import OpenAICompatibleExtractor
from interview_intelligence.providers.budget import CallBudget
from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.search.reranker import LLMReranker


def main():
    settings = load_settings()
    validate_backend_model_endpoint(settings.model_base_url or "")
    validate_model_preflight(api_key=settings.model_api_key,
                             embedding_dimension=settings.embedding_dimension,
                             max_calls=settings.max_model_calls,
                             max_tokens=settings.max_model_tokens)
    if not settings.model_base_url or not settings.extraction_model or not settings.embedding_model:
        raise ValueError("MODEL_CONFIGURATION_INCOMPLETE")
    budget = CallBudget(settings.max_model_calls, settings.max_model_tokens)
    gate = ModelCallGate(settings.model_lock_path,
                         minimum_interval_seconds=settings.model_min_interval_seconds)
    encoder_class = (ArkMultimodalEncoder if settings.embedding_model == "doubao-embedding-vision"
                     else OpenAICompatibleEncoder)
    encoder = encoder_class(model=settings.embedding_model, dimension=settings.embedding_dimension,
                            api_key=settings.model_api_key, base_url=settings.model_base_url,
                            budget=budget, call_gate=gate,
                            timeout_seconds=settings.model_request_timeout_seconds)
    vector = encoder.embed("Redis 为什么快？")
    extractor = OpenAICompatibleExtractor(model=settings.extraction_model,
                                           api_key=settings.model_api_key,
                                           base_url=settings.model_base_url,
                                           max_attempts=1, budget=budget, call_gate=gate,
                                           timeout_seconds=settings.model_request_timeout_seconds,
                                           stream=settings.extraction_stream)
    sample = "# Redis 一面\nRedis 为什么快？"
    result = extractor.extract(text=sample, revision_id="preflight")
    judge = OpenAICompatibleJudge(model=settings.judge_model, api_key=settings.model_api_key,
                                  base_url=settings.model_base_url, max_attempts=1,
                                  budget=budget, call_gate=gate,
                                  timeout_seconds=settings.model_request_timeout_seconds)
    judgement = judge.judge("Redis 为什么快？", "Redis 快的原因是什么？")
    batch = judge.judge_many("Redis 为什么快？", [
        ("q1", "Redis 快的原因是什么？"), ("q2", "Spring 事务传播有哪些？"),
    ])
    reranker = LLMReranker(model=settings.reranker_model, api_key=settings.model_api_key,
                          base_url=settings.model_base_url, budget=budget, call_gate=gate,
                          timeout_seconds=settings.model_request_timeout_seconds)
    ranked = reranker.rerank("Redis 为什么快", [
        {"canonical_question_id": "q1", "canonical_text": "Redis 为什么快？"},
        {"canonical_question_id": "q2", "canonical_text": "Spring 事务传播有哪些？"},
    ])
    report = {"embedding_dimension": len(vector),
                      "embedding_version": encoder.version,
                      "structured_output_schema_version": result.schema_version,
                      "judge_decision": judgement.decision,
                      "batch_judge_decisions": {key: value.decision for key, value in batch.items()},
                      "reranker_first_id": ranked[0]["canonical_question_id"],
                      "max_in_flight": 1,
                      "minimum_interval_seconds": settings.model_min_interval_seconds,
                      "model_calls": budget.used_calls,
                      "tokens_observed": budget.used_tokens}
    output = Path("data/reports/model-preflight.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
