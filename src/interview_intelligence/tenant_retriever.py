"""Shared corpus encoder with account-selected dialogue/reranking credentials."""
import hashlib
from threading import RLock

import httpx
from openai import OpenAI

from interview_intelligence.tenant import current_provider
from interview_intelligence.tenant_models import TenantAccount
from interview_intelligence.search.elasticsearch import ElasticsearchRetriever
from interview_intelligence.search.reranker import LLMReranker
from interview_intelligence.providers.tenant_transport import public_sync_transport


class PersonalReranker(LLMReranker):
    """Never persist or expose a tenant provider's raw validation/error payload."""
    def rerank(self, query, candidates):
        try:
            return super().rerank(query, candidates)
        except Exception as error:
            safe_codes = {"QUERY_MODEL_BUDGET_EXCEEDED", "QUERY_TOKEN_BUDGET_EXCEEDED", "QUERY_CANCELLED",
                "QUERY_DEADLINE_EXCEEDED", "MODEL_CALL_BUDGET_EXCEEDED", "MODEL_TOKEN_BUDGET_EXCEEDED",
                "STREAM_INCOMPLETE", "MODEL_OUTPUT_TRUNCATED"}
            # Older deployed rerankers wrap their underlying budget failure.
            # Recover only exact host codes; no provider payload is exposed.
            candidate, visited = error, set()
            for _ in range(8):
                if candidate is None or id(candidate) in visited:
                    break
                visited.add(id(candidate))
                if isinstance(candidate, ValueError) and str(candidate) in safe_codes:
                    raise ValueError(str(candidate)) from None
                candidate = candidate.__cause__ or candidate.__context__
            raise ValueError("MODEL_PROVIDER_UNAVAILABLE") from None


class TenantRetriever:
    def __init__(self, base, tenant_service, identifier, gate, on_call):
        self.base, self.tenant_service, self.identifier = base, tenant_service, identifier
        self.gate, self.on_call, self.cache, self.lock = gate, on_call, {}, RLock()

    def selected(self):
        if not isinstance(self.base, ElasticsearchRetriever):
            # Injected deterministic test/backends have no provider clients.
            return self.base
        selected = current_provider.get()
        if not selected:
            with self.tenant_service.database.session() as session:
                values = self.tenant_service.provider_values(session.get(TenantAccount, self.identifier))
            selected = {"source": values["source"], "settings": self.tenant_service.runtime_settings(values)}
        settings, source = selected["settings"], selected["source"]
        key = (source, settings.model_base_url, settings.reranker_model,
               hashlib.sha256((settings.model_api_key or "").encode()).hexdigest())
        with self.lock:
            if key not in self.cache:
                reranker = None
                if settings.model_api_key and settings.model_base_url and settings.reranker_model:
                    client = OpenAI(api_key=settings.model_api_key, base_url=settings.model_base_url,
                        timeout=settings.model_request_timeout_seconds, max_retries=0,
                        http_client=httpx.Client(trust_env=False, timeout=settings.model_request_timeout_seconds,
                            transport=public_sync_transport() if source == "personal" else None))
                    reranker_class = PersonalReranker if source == "personal" else LLMReranker
                    reranker = reranker_class(model=settings.reranker_model, client=client,
                        base_url=settings.model_base_url, call_gate=self.gate, on_call=self.on_call,
                        timeout_seconds=settings.model_request_timeout_seconds, stream=True)
                self.cache[key] = ElasticsearchRetriever("", self.base.encoder, reranker=reranker,
                    client=self.base.client, alias=self.base.alias)
            return self.cache[key]

    def __getattr__(self, name):
        return getattr(self.selected(), name)
