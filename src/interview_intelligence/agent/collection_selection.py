"""Host-owned quantity preview and lossless category browsing.

The model cannot grant a selection or invent category membership. Choices are
resolved against the persisted original request and a current SQL manifest.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import re

from sqlalchemy import select

from interview_intelligence.agent.query_contract import QuerySpec, StructuredQueryRequest
from interview_intelligence.agent.query_scope import VERSION as USER_SCOPE_VERSION, continues_scope, user_messages
from interview_intelligence.analytics.engineering_categories import engineering_collection_categories
from interview_intelligence.analytics.listing import ListRequest, ranked_query
from interview_intelligence.analytics.scope import verify_scope
from interview_intelligence.analytics.semantic_scope import ENGINEERING_DESIGN_TASK, load_semantic_scope


VERSION = "collection_selection_v1"
ALL = "全量分页"
CATEGORIES = "按分类浏览"
_COUNT = re.compile(r"多少|几道|数量|总数|总共|一共")
_BARE_COUNT = re.compile(r"(?:这些题|这类题|这批题|它们|题目|题|一共|总共|有|多少|几道|数量|总数|提问|次数|记录|出现|的|和|及|与|[，,？?\s])+")


@dataclass
class SelectionDecision:
    plan: QuerySpec
    metadata: dict | None = None
    category_id: str | None = None
    canonical_ids: tuple[str, ...] | None = None
    trusted: bool = False


def _request(plan):
    return ListRequest(**plan.filters.model_dump(), semantic_task=ENGINEERING_DESIGN_TASK,
        sort=plan.sort, top_n=plan.top_n, page_size=plan.page_size,
        review_statuses=plan.review_statuses, review_order=plan.review_order)


def _clean_message(value):
    return value.strip().rstrip("。！!？? ")


class CollectionSelection:
    def __init__(self, service, *, threshold=100):
        self.service, self.threshold = service, threshold

    @staticmethod
    def saved(run):
        value = run.state.get("collection_selection") or {}
        return value if value.get("version") == VERSION else {}

    def resolve(self, run):
        """Resolve only persisted host choices before invoking any planner."""
        saved = self.saved(run)
        if not saved or isinstance(run.request, StructuredQueryRequest):
            return None
        current = _clean_message(run.request.message)
        matches = {item["message"]: item for item in saved.get("choices", [])}
        event = None
        if current in {ALL, "全量", "全部", "全部分页", "查看全量", "查看全部"}:
            event = {"kind": "all"}
        elif current == CATEGORIES:
            event = {"kind": "categories"}
        elif current in matches and matches[current].get("category_id"):
            event = {"kind": "category", "id": matches[current]["category_id"]}
        elif current.startswith("查看分类：") or current.startswith("查看分类:"):
            event = {"kind": "categories"}
        elif _COUNT.search(current) and _BARE_COUNT.fullmatch(current):
            event = {"kind": "count"}
        elif saved.get("stage") in {"scope", "categories", "unselected"}:
            if current in {"下一页", "下页", "继续"}:
                event = {"kind": "repeat"}
            elif _COUNT.search(current) and continues_scope(run):
                event = {"kind": "count"}
        if event is None:
            return None
        root = QuerySpec.model_validate(saved["root_plan"])
        filters = root.filters.model_copy(update=self.service._explicit_filters(run.request))
        run.collection_event = event
        return root.model_copy(update={"filters": filters,
            "page_size": run.request.page_size if "page_size" in run.request.model_fields_set else root.page_size,
            "top_n": None if event["kind"] == "count" else root.top_n,
            "action": "COUNT" if event["kind"] == "count" else "LIST", "final": True})

    def _preview(self, run, root, *, as_of=None):
        run.limits.check()
        as_of = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of or self.service.today()
        with self.service.database.session() as session:
            if session.bind.dialect.name == "postgresql":
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            if self.service._revisions(session) != run.revisions:
                raise ValueError("SNAPSHOT_CHANGED")
            scope = load_semantic_scope(session, root.filters, task=ENGINEERING_DESIGN_TASK)
            chosen, _ = ranked_query(session, _request(root), self.service.user_id,
                as_of, semantic_scope=scope)
            ids = tuple(session.scalars(select(chosen.c.key)))
            preview = engineering_collection_categories(session, root.filters,
                semantic_scope=scope, canonical_ids=ids)
            preview["as_of"] = as_of.isoformat()
        run.limits.check()
        return preview

    @staticmethod
    def _choices(preview, stage):
        result = [{"message": ALL, "label": ALL, "kind": "all", "counts": preview["counts"]}]
        if stage == "categories":
            for item in preview["categories"]:
                result.append({"message": "查看分类：" + item["label"], "label": item["label"],
                    "kind": {"TOPIC_L1": "category", "UNCLEAR": "uncategorized", "PENDING": "pending"}[item["kind"]],
                    "category_id": item["id"], "counts": item["counts"]})
        else:
            result.append({"message": CATEGORIES, "label": CATEGORIES, "kind": "category"})
        return result

    @staticmethod
    def _public(saved, preview, stage):
        result = {"version": VERSION, "stage": stage, "counts": preview["counts"],
            "choices": deepcopy(saved["choices"]), "classification_notice": preview["classification_notice"]}
        if saved.get("selected"):
            result["selected"] = saved["selected"]
        return result

    def _store(self, run, root, preview, stage, *, previous=None, selected=None):
        previous = previous or {}
        messages = previous.get("user_messages") or user_messages(run)
        explicit = {**previous.get("explicit_filters", {}), **self.service._explicit_filters(run.request)}
        saved = {"version": VERSION, "stage": stage, "root_plan": root.model_dump(mode="json"),
            "user_messages": messages, "explicit_filters": explicit,
            "original_message": previous.get("original_message", run.request.message),
            "revisions": list(run.revisions), "category_hash": preview["category_hash"],
            "as_of": preview["as_of"],
            "choices": self._choices(preview, "categories" if stage == "categories" else "scope")}
        if selected:
            saved["selected"] = selected
            # Keep every host category reachable after a selection, including
            # switching from a category back to the original all scope.
            saved["choices"] = self._choices(preview, "categories")
        run.state["collection_selection"] = saved
        run.state["query_scope_intent"] = {"version": USER_SCOPE_VERSION,
            "messages": messages, "explicit_filters": explicit}
        if stage in {"scope", "categories", "unselected"}:
            for key in ("list_request", "list_goal", "result_set_id", "last_plan"):
                run.state.pop(key, None)
            run.state["current_page_ids"], run.state["current_page_summary"] = [], []
            run.state["conversation_focus"] = {"intent": "LIST", "semantic_task": ENGINEERING_DESIGN_TASK,
                "message": saved["original_message"], "filters": root.filters.model_dump(mode="json"),
                "sort": root.sort, "top_n": root.top_n, "review_statuses": root.review_statuses,
                "review_order": root.review_order, "group_by": "question", "topic_level": "L1"}
        return saved

    def _clarify(self, run, root, preview, stage, *, previous=None, changed=False):
        saved = self._store(run, root, preview, stage, previous=previous)
        counts = preview["counts"]
        question = ("当前集合已更新，请重新选择浏览方式。" if changed else "")
        question += (f"当前范围可浏览 {counts['visible']} 道题，其中已确认 {counts['confirmed']} 道，"
            f"{counts['pending']} 道含待核验提问。")
        question += ("请选择已有主题分类；分类不明确和待核验题分别保留。" if stage == "categories" else
            "题目较多，请选择按分类浏览或全量分页。")
        plan = QuerySpec(action="CLARIFY", filters=root.filters, clarification=question,
            clarification_options=[ALL, CATEGORIES] if stage == "scope" else [ALL])
        return SelectionDecision(plan, self._public(saved, preview, stage), trusted=True)

    def prepare(self, run, plan):
        """Preview once and preserve an explicit all/category choice per scope."""
        saved, event = self.saved(run), run.collection_event
        if isinstance(run.request, StructuredQueryRequest):
            params = run.request.list_request
            if params.semantic_task != ENGINEERING_DESIGN_TASK:
                run.state.pop("collection_selection", None)
                return SelectionDecision(plan)
            same_scope = saved and saved["root_plan"]["filters"] == plan.filters.model_dump(mode="json")
            grouped = getattr(params, "group_by", "question") != "question"
            if grouped and same_scope and saved.get("selected"):
                payload = verify_scope(params.cursor, key=self.service.signing_key,
                    corpus_revision=run.revisions[0]) if params.cursor else {}
                external = payload.get("canonical_scope_hash") is not None if params.cursor else (
                    saved["selected"]["kind"] == "category" or saved["root_plan"].get("top_n") is not None)
                if external:
                    root = QuerySpec.model_validate(saved["root_plan"])
                    preview = self._preview(run, root, as_of=saved.get("as_of"))
                    selected = saved["selected"]
                    ids = tuple(identity for item in preview["categories"] for identity in item["canonical_ids"])
                    if selected["kind"] == "category":
                        category = next((item for item in preview["categories"] if item["id"] == selected["id"]), None)
                        if category is None:
                            raise ValueError("SNAPSHOT_CHANGED")
                        ids = tuple(category["canonical_ids"])
                    state = self._store(run, root, preview, "selected", previous=saved, selected=selected)
                    return SelectionDecision(plan, self._public(state, preview, "selected"), canonical_ids=ids)
            root = plan.model_copy(update={"action": "LIST", "group_by": "question"})
            preview = self._preview(run, root)
            same = saved and saved["root_plan"]["filters"] == root.filters.model_dump(mode="json")
            selected = {"kind": "all", "label": ALL}
            if params.collection_category:
                category = next((item for item in preview["categories"] if item["id"] == params.collection_category), None)
                if category is None:
                    raise ValueError("INVALID_COLLECTION_CATEGORY")
                selected = {"kind": "category", "id": category["id"], "label": category["label"]}
            state = self._store(run, root, preview, "selected", previous=saved if same else None, selected=selected)
            state["authorization_source"] = "structured_request"
            return SelectionDecision(plan)
        if plan.semantic_task != ENGINEERING_DESIGN_TASK or plan.action not in {"LIST", "NEXT", "COUNT", "STATS"}:
            if not continues_scope(run) and not event:
                run.state.pop("collection_selection", None)
            return SelectionDecision(plan)
        inherited = bool(saved and (event or continues_scope(run) or run.requery_origin
            or run.request.message.strip() == saved.get("original_message")))
        same_filters = bool(inherited and saved["root_plan"]["filters"] == plan.filters.model_dump(mode="json")
            and saved["root_plan"]["review_statuses"] == plan.review_statuses
            and saved["root_plan"]["review_order"] == plan.review_order)
        root = QuerySpec.model_validate(saved["root_plan"]) if same_filters else plan.model_copy(update={"action": "LIST", "group_by": "question"})
        if same_filters and plan.action == "LIST":
            root = root.model_copy(update={"sort": plan.sort, "page_size": plan.page_size, "top_n": plan.top_n})
        if plan.action in {"COUNT", "STATS"} and not same_filters:
            if saved:
                preview = self._preview(run, root)
                self._store(run, root, preview, "unselected")
            return SelectionDecision(plan)
        # Small ordinary lists require no semantic facet scan or extra menu.
        preview = self._preview(run, root, as_of=saved.get("as_of") if same_filters
            and (event or plan.action in {"COUNT", "STATS", "NEXT"}) else None)
        if event and not same_filters:
            return self._clarify(run, root, preview, "scope", previous=saved, changed=True)
        if event and same_filters:
            stale = saved["revisions"] != list(run.revisions) or saved["category_hash"] != preview["category_hash"]
            if stale and event["kind"] not in {"repeat", "count"}:
                return self._clarify(run, root, preview, "scope", previous=saved, changed=True)
            if event["kind"] in {"categories", "repeat"}:
                stage = "categories" if event["kind"] == "categories" else (
                    "categories" if saved.get("stage") == "categories" else "scope")
                return self._clarify(run, root, preview, stage, previous=saved)
            if event["kind"] == "all":
                selected = {"kind": "all", "label": ALL}
            elif event["kind"] == "category":
                category = next((item for item in preview["categories"] if item["id"] == event["id"]), None)
                if category is None:
                    return self._clarify(run, root, preview, "categories", previous=saved, changed=True)
                selected = {"kind": "category", "id": category["id"], "label": category["label"]}
            else:
                selected = saved.get("selected")
        else:
            selected = saved.get("selected") if same_filters and saved.get("stage") == "selected" else None
        if plan.action == "NEXT" and same_filters and (saved["revisions"] != list(run.revisions)
                or saved["category_hash"] != preview["category_hash"]):
            raise ValueError("SNAPSHOT_CHANGED")
        if selected:
            state = self._store(run, root, preview, "selected", previous=saved, selected=selected)
            ids = tuple(identity for item in preview["categories"] for identity in item["canonical_ids"])
            category_id = None
            if selected["kind"] == "category":
                category = next((item for item in preview["categories"] if item["id"] == selected["id"]), None)
                if category is None:
                    return self._clarify(run, root, preview, "categories", previous=saved, changed=True)
                category_id, ids = category["id"], tuple(category["canonical_ids"])
            grouped = plan.action == "STATS" or plan.action == "NEXT" and plan.group_by != "question"
            return SelectionDecision(plan, self._public(state, preview, "selected"), None if grouped else category_id,
                ids if plan.action == "COUNT" or grouped else None, trusted=bool(event))
        if plan.action == "LIST" and preview["counts"]["visible"] > self.threshold:
            return self._clarify(run, root, preview, "scope", previous=saved if same_filters else None)
        return SelectionDecision(plan)
