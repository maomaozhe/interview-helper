"""Durable collection state. Uncertain model or HTTP calls are never replayed."""
from __future__ import annotations

import os
from pathlib import Path

from eval.common import digest, read_json, read_jsonl, write_json, write_jsonl
from eval.validate_gold import require


def context_record(context):
    return {**{key: context.get(key) for key in ("user_id", "conversation_count", "state_count", "event_count")},
            "conversation_ledger_sha256": context.get("conversation_ledger_sha256"),
            "states_sha256": digest(context.get("states", [])),
            "events_sha256": digest(context.get("write_events", []))}


def runtime_record(context):
    # These are observations, not proof of the remote source/image revision.
    return {key: context.get(key) for key in ("user_id", "snapshot", "query_max_model_calls", "query_max_tokens",
            "query_planning_max_calls", "request_max_model_calls", "request_max_tokens",
            "query_prompt_version", "model_gate_shared", "as_of_frozen", "runtime_fingerprint",
            "runtime_evidence", "runtime_verification", "model_gate_identity", "multi_tenant_enabled", "provider_profile")}


class CollectionCheckpoint:
    def __init__(self, output, identity, *, budget=None, resume=False):
        self.output, self.identity = Path(output), identity
        self.path = self.output.with_suffix(".checkpoint.json")
        self.partial = self.output.with_suffix(".partial.jsonl")
        self.receipt = self.output.with_suffix(".collection.json")
        self.budget, self.resume, self.descriptor = budget, resume, None

    def __enter__(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.descriptor = os.open(self.output.with_suffix(".collection.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.descriptor)
            self.descriptor = None
            require(False, "COLLECTION_ALREADY_RUNNING: another process owns this output")
        try:
            self._open()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_):
        if self.budget is not None:
            self.budget.on_change = None
        if self.descriptor is not None:
            os.close(self.descriptor)  # OS lock also releases after a process crash.
            self.descriptor = None

    def _open(self):
        if self.resume:
            require(self.path.is_file(), "COLLECTION_CHECKPOINT_MISSING: old partial files are not resumable")
            try:
                envelope = read_json(self.path)
                self.state = envelope["state"]
                require(digest(self.state) == envelope["sha256"], "COLLECTION_CHECKPOINT_CORRUPT")
                require(self.state["schema"] == "collection_checkpoint_v1", "COLLECTION_CHECKPOINT_SCHEMA")
                require(self.state["identity"] == self.identity, "COLLECTION_IDENTITY_CHANGED")
                require(isinstance(self.state["rows"], list), "COLLECTION_ROWS_CORRUPT")
                require([r["id"] for r in self.state["rows"]] == self.identity["sample_ids"][:len(self.state["rows"])],
                        "COLLECTION_ROWS_CHANGED")
                require(len(self.state["rows"]) <= len(self.identity["sample_ids"]), "COLLECTION_ROWS_CHANGED")
                for artifact in (self.partial, self.output):
                    if artifact.exists():
                        saved = read_jsonl(artifact)
                        require(saved == self.state["rows"][:len(saved)] and len(saved) <= len(self.state["rows"]),
                                "COLLECTION_PREDICTIONS_CHANGED")
                        if artifact == self.output:
                            require(self.complete and saved == self.state["rows"], "COLLECTION_FINAL_OUTPUT_CHANGED")
                if self.budget is not None:
                    self.budget.restore(self.state["budget"])
                else:
                    require(self.state["budget"] is None, "COLLECTION_BUDGET_CHANGED")
            except (KeyError, TypeError, IndexError) as error:
                raise ValueError("COLLECTION_CHECKPOINT_CORRUPT") from error
            require(not self.state.get("uncertain"),
                    "COLLECTION_UNCERTAIN: inspect persisted request telemetry; automatic replay is forbidden")
            require(not self.state.get("binding_error"), "COLLECTION_RUNTIME_CHANGED: inspect saved observations")
            if self.state.get("inflight"):
                require(self.identity["adapter"] in {"sql", "published"},
                        "COLLECTION_INFLIGHT_UNRESOLVED: retain budget and inspect provider/request evidence; do not replay")
                self.state["inflight"] = None  # Only side-effect-free reads may restart.
                self.save()
        else:
            require(not any(p.exists() for p in (self.path, self.partial, self.output, self.receipt)),
                    "PREDICTION_OUTPUT_ALREADY_EXISTS: use --resume with the original checkpoint")
            self.state = {"schema": "collection_checkpoint_v1", "identity": self.identity, "rows": [],
                          "budget": self.budget.state() if self.budget else None, "inflight": None,
                          "uncertain": False, "runtime": None, "http_context": None, "status": "collecting",
                          "snapshot": None, "binding_error": None}
            self.save()
        if self.budget is not None:
            self.budget.on_change = self.update_budget

    @property
    def complete(self):
        return (len(self.state["rows"]) == len(self.identity["sample_ids"])
                and not self.state.get("inflight") and not self.state.get("uncertain")
                and not self.state.get("binding_error"))

    def save(self):
        write_json(self.path, {"state": self.state, "sha256": digest(self.state)})

    def update_budget(self, state):
        self.state["budget"] = state
        self.save()

    def bind_runtime(self, context, run_id):
        runtime = {"observed": runtime_record(context), "request_namespace": run_id}
        def valid_ledger(value):
            return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
        if self.state["runtime"] is not None:
            require(valid_ledger((self.state["http_context"] or {}).get("conversation_ledger_sha256")),
                    "COLLECTION_LEGACY_HTTP_CHECKPOINT: conversation ledger unavailable; automatic resume is forbidden")
        require(valid_ledger(context.get("conversation_ledger_sha256")), "EVALUATION_CONVERSATION_LEDGER_REQUIRED")
        if self.state["runtime"] is not None:
            require(self.state["runtime"]["observed"] == runtime["observed"], "COLLECTION_RUNTIME_CHANGED")
            require(self.state["http_context"] == context_record(context), "COLLECTION_HTTP_STATE_CHANGED")
        else:
            self.state["runtime"] = runtime
            self.state["http_context"] = context_record(context)
            self.save()
        return self.state["runtime"]["request_namespace"]

    def bind_snapshot(self, snapshot):
        if self.state["snapshot"] is not None:
            require(self.state["snapshot"] == snapshot, "COLLECTION_SNAPSHOT_CHANGED")
        else:
            self.state["snapshot"] = snapshot
            self.save()

    def verify_candidate(self, *, final=False):
        candidate = self.identity.get("candidate_binding") or {}
        component = candidate.get("components", {}).get(self.identity["section"], candidate)
        calls = [call for row in self.state["rows"] for call in row.get("model_calls", [])]
        primary = {"extraction": "EXTRACTION", "task_labels": "TASK_ANNOTATION", "dedup": "DEDUP_JUDGE",
                   "retrieval": "RERANK", "routing": "QUERY_PLAN"}.get(self.identity["section"])
        checked = False
        for declaration, field, operation in (("model", "model", primary), ("prompt_version", "prompt_version", primary),
                ("embedding_model", "model", "EMBEDDING"), ("reranker_model", "model", "RERANK"),
                ("model_revision", "model_revision", primary),
                ("embedding_model_revision", "model_revision", "EMBEDDING"),
                ("reranker_model_revision", "model_revision", "RERANK")):
            if component.get(declaration) is not None:
                relevant = [c for c in calls if c.get("operation_type") == operation]
                require((not final or field != "model_revision" or bool(relevant))
                        and all(c.get(field) == component[declaration] for c in relevant),
                        f"CANDIDATE_OBSERVATION_MISMATCH: {declaration}")
                checked = checked or bool(relevant)
        expected_runtime = component.get("runtime_fingerprint", candidate.get("runtime_fingerprint"))
        if expected_runtime is not None:
            actual = (self.state["runtime"] or {}).get("observed", {}).get("runtime_fingerprint") if self.identity["adapter"] == "http" else self.identity["provenance"]["source_sha256"]
            require(actual == expected_runtime, "CANDIDATE_RUNTIME_NOT_VERIFIED")
        return "observed_models" if checked else "declared_only"

    def begin(self, key):
        require(self.state["inflight"] is None and not self.state["uncertain"], "COLLECTION_NOT_READY")
        require(self.identity["sample_ids"][len(self.state["rows"])] == key, "COLLECTION_SAMPLE_ORDER_CHANGED")
        self.state["inflight"] = {"id": key}
        self.state["status"] = "collecting"
        self.save()

    def pause(self):
        require(not self.state["inflight"] and not self.state["uncertain"], "COLLECTION_NOT_READY")
        self.state["status"] = "paused"
        self.save()
        return {"schema": "collection_progress_v1", "status": "PAUSED",
                "samples": len(self.state["rows"]), "total_samples": len(self.identity["sample_ids"]),
                "failed": sum(bool(row["failed"]) for row in self.state["rows"]), "budget_state": self.state["budget"]}

    def append(self, row, *, context=None):
        require(self.state["inflight"] == {"id": row["id"]}, "COLLECTION_SAMPLE_ORDER_CHANGED")
        if context is not None and self.state["runtime"] is not None:
            if runtime_record(context) != self.state["runtime"]["observed"]:
                row.update(failed=True, error_code="COLLECTION_RUNTIME_CHANGED")
                self.state["binding_error"] = "COLLECTION_RUNTIME_CHANGED"
        self.state["rows"].append(row)
        self.state["inflight"] = None
        self.state["uncertain"] = bool(row.get("telemetry_unavailable"))
        if context is not None:
            self.state["http_context"] = context_record(context)
        self.save()  # Authoritative row+budget transition precedes the derived JSONL.
        write_jsonl(self.partial, self.state["rows"])
        require(not self.state.get("binding_error"), "COLLECTION_RUNTIME_CHANGED: observation retained; no further calls")
        require(not self.state["uncertain"],
                "COLLECTION_UNCERTAIN: request or call ledger is incomplete; inspect telemetry before any new collection")

    def finalize(self):
        require(self.complete, "COLLECTION_NOT_COMPLETE")
        version_binding = self.verify_candidate(final=True)
        rows, identity = self.state["rows"], self.identity
        write_jsonl(self.output, rows)
        self.state["status"] = "complete"
        self.save()
        candidate = identity.get("candidate_binding")
        receipt = {"schema": "collection_v2", **{key: identity[key] for key in ("adapter", "section", "split", "review_policy")},
            "snapshot": self.state["snapshot"] or identity["snapshot"],
            "samples": len(rows), "failed": sum(bool(r["failed"]) for r in rows),
            "budget": {k: v for k, v in self.state["budget"].items() if k not in {"reservation", "per_call_tokens"}}
                      if self.state["budget"] else {"model_calls": 0},
            "budget_state": self.state["budget"],
            "provenance": identity["provenance"], "dataset_sha256": identity["dataset_sha256"],
            "selection_sha256": identity["selection_sha256"], "collection_identity_sha256": digest(identity),
            "candidate_binding": candidate, "candidate_sha256": digest(candidate) if candidate is not None else None,
            "version_binding": version_binding,
            "runtime_verification": ((self.state["runtime"] or {}).get("observed", {}).get("runtime_verification",
                                     "REMOTE_SOURCE_NOT_VERIFIED") if identity["adapter"] == "http" else "LOCAL_SOURCE_BOUND"),
            "runtime_context": self.state["runtime"],
            "provider_fingerprints": list({digest(r["provider_fingerprint"]): r["provider_fingerprint"]
                                          for r in rows if r.get("provider_fingerprint") is not None}.values()),
            "predictions_sha256": digest(self.output.read_bytes())}
        if self.receipt.exists():
            require(read_json(self.receipt) == receipt, "COLLECTION_RECEIPT_CHANGED")
        else:
            write_json(self.receipt, receipt)
        return receipt
