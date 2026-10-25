"""Exact SQL category lists and global ranking with revision-bound pagination."""
from __future__ import annotations

from datetime import date
import hashlib
import json
import time
from typing import Literal

from pydantic import Field, model_validator
from sqlalchemy import distinct, func, select, case, and_, or_

from interview_intelligence.analytics.scope import sign_scope, verify_scope
from interview_intelligence.analytics.engineering_categories import engineering_collection_categories
from interview_intelligence.analytics.ranking import (
    SCORING_VERSIONS, importance_band, importance_score, sql_gap_score, sql_score_components,
)
from interview_intelligence.analytics.semantic_scope import (
    ENGINEERING_DESIGN_TASK, SemanticScope, SemanticTask, load_semantic_scope, semantic_base_filters,
)
from interview_intelligence.analytics.stats import _active_from, _conditions, _effective_date, _group_key, subtract_calendar_months
from interview_intelligence.contracts import FilterSpec, StatsRequest
from interview_intelligence.domain.models import (
    CanonicalQuestion, CorpusState, Interview, OccurrenceTaskAnnotation,
    QuestionOccurrence, SourceDocument, SourceRevision, UserQuestionState, UserRevision,
)


class ListRequest(FilterSpec):
    semantic_task: SemanticTask | None = None
    collection_category: str | None = Field(default=None, max_length=64)
    sort: Literal["frequency", "importance", "gap"] = "frequency"
    top_n: int | None = Field(default=None, ge=1, le=1000)
    page_size: int = Field(default=20, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=4096)
    review_statuses: list[Literal["UNSEEN","WEAK","REVIEWED","MASTERED"]] = Field(default_factory=list,max_length=4)
    review_order: Literal["BEFORE_TOP_N","AFTER_TOP_N"] = "BEFORE_TOP_N"

    @model_validator(mode="after")
    def validate_semantic_task_filters(self):
        if self.semantic_task:
            semantic_base_filters(filter_fields(self), task=self.semantic_task)
        if self.collection_category is not None and self.semantic_task != ENGINEERING_DESIGN_TASK:
            raise ValueError("COLLECTION_CATEGORY_REQUIRES_ENGINEERING_DESIGN")
        return self


class StatsListRequest(ListRequest):
    group_by: Literal["question","topic","company","round"] = "question"
    topic_level: Literal["L1","L2"] = "L1"

    @model_validator(mode="after")
    def validate_group_scope(self):
        if self.collection_category is not None and self.group_by != "question":
            raise ValueError("COLLECTION_CATEGORY_REQUIRES_QUESTION_LIST")
        if self.group_by != "question" and (self.review_statuses or self.sort != "frequency"):
            raise ValueError("Group statistics support frequency sorting without question review filters")
        return self


def filter_fields(request):
    return FilterSpec.model_validate({k:getattr(request,k) for k in FilterSpec.model_fields})


def ranked_query(session, request, user_id, as_of, *, semantic_scope: SemanticScope | None = None,
                 canonical_ids: tuple[str, ...] | None = None):
    """All aggregation, score normalization, Top N and review set order live in SQL."""
    grouped_by = getattr(request,"group_by","question")
    f = filter_fields(request)
    conditions = _conditions(f)
    if request.semantic_task:
        f = semantic_base_filters(f, task=request.semantic_task)
        semantic_scope = semantic_scope or load_semantic_scope(session, f, task=request.semantic_task)
        conditions = [*_conditions(f), QuestionOccurrence.id.in_(semantic_scope.selected_occurrence_ids)]
    if canonical_ids is not None:
        conditions.append(QuestionOccurrence.canonical_question_id.in_(canonical_ids))
    engineering = semantic_scope is not None and semantic_scope.task == ENGINEERING_DESIGN_TASK
    confirmed = QuestionOccurrence.id.in_(semantic_scope.accepted_occurrence_ids) if engineering else None
    key = _group_key(StatsRequest(**f.model_dump(),group_by=grouped_by,
        topic_level=getattr(request,"topic_level","L1")))
    effective = _effective_date(f.date_basis)
    recent = and_(effective>=subtract_calendar_months(as_of,3),effective<as_of.fromordinal(as_of.toordinal()+1))
    g = select(key.label("key"),
        (func.sum(case((confirmed, 1), else_=0)) if engineering else func.count()).label("occurrence_count"),
        func.count(distinct(case((confirmed, Interview.id)) if engineering else Interview.id)).label("interview_count"),
        func.count(distinct(case((confirmed, SourceDocument.id)) if engineering else SourceDocument.id)).label("source_document_count"),
        func.count(distinct(case((confirmed, Interview.company_normalized)) if engineering else Interview.company_normalized)).label("company_count"),
        func.sum(case((and_(recent, confirmed) if engineering else recent,1),else_=0)).label("recent_count"),
        (func.coalesce(func.min(case((confirmed, QuestionOccurrence.id))), func.min(QuestionOccurrence.id))
            if engineering else func.min(QuestionOccurrence.id)).label("witness_id"),
        *([func.count().label("selected_occurrence_count"),
            func.sum(case((confirmed, 0), else_=1)).label("pending_occurrence_count")]
            if engineering else [])
    ).select_from(_active_from()).where(*conditions).group_by(key).subquery()
    known_conditions = [*conditions, QuestionOccurrence.id.in_(semantic_scope.accepted_occurrence_ids)] if engineering else conditions
    known = session.scalar(select(func.count(distinct(Interview.company_normalized))).select_from(
        _active_from()).where(*known_conditions)) or 0
    components = sql_score_components(g, known_companies=known)
    importance = importance_score(components)
    status = func.coalesce(UserQuestionState.status,"UNSEEN") if grouped_by=="question" else "UNSEEN"
    base = select(g,importance.label("importance_score"),components["frequency"].label("frequency_component"),
        components["company_coverage"].label("coverage_component"),components["recent_frequency"].label("recent_component"))
    if grouped_by=="question":
        base=base.add_columns(status.label("user_status")).outerjoin(UserQuestionState,
            and_(UserQuestionState.canonical_question_id==g.c.key,UserQuestionState.user_id==user_id))
    else:
        from sqlalchemy import literal
        base=base.add_columns(literal("UNSEEN").label("user_status"))
    b=base.subquery()
    gap=sql_gap_score(b.c.importance_score, b.c.user_status)
    score=b.c.occurrence_count if request.sort=="frequency" else b.c.importance_score if request.sort=="importance" else gap
    scored=select(b,gap.label("gap_score"),score.label("rank_score")).subquery()
    eligible=select(scored)
    if request.review_statuses and request.review_order=="BEFORE_TOP_N":
        eligible=eligible.where(scored.c.user_status.in_(request.review_statuses))
    e=eligible.subquery()
    total=session.scalar(select(func.count()).select_from(e)) or 0
    ordered=select(e,func.row_number().over(order_by=(e.c.rank_score.desc(),e.c.key)).label("rank")).subquery()
    chosen=select(ordered)
    if request.top_n: chosen=chosen.where(ordered.c.rank<=request.top_n)
    if request.review_statuses and request.review_order=="AFTER_TOP_N":
        chosen=chosen.where(ordered.c.user_status.in_(request.review_statuses))
    return chosen.subquery(),total


def task_coverage(session, filters: FilterSpec) -> dict:
    """Coverage is measured before task filtering, on the same occurrence scope."""
    base = filters.model_copy(update={"response_form": None, "coding_focus": None,"annotation_status":None})
    total = session.scalar(select(func.count()).select_from(_active_from()).where(*_conditions(base))) or 0
    annotated = session.scalar(select(func.count()).select_from(_active_from()).where(
        *_conditions(base), select(OccurrenceTaskAnnotation.occurrence_id).where(
            OccurrenceTaskAnnotation.occurrence_id == QuestionOccurrence.id).exists())) or 0
    classified = session.scalar(select(func.count()).select_from(_active_from()).where(
        *_conditions(base), select(OccurrenceTaskAnnotation.occurrence_id).where(
            OccurrenceTaskAnnotation.occurrence_id == QuestionOccurrence.id,
            OccurrenceTaskAnnotation.response_form != "UNKNOWN",
            OccurrenceTaskAnnotation.coding_focus != "UNKNOWN").exists())) or 0
    verified=session.scalar(select(func.count()).select_from(_active_from()).where(*_conditions(base),
        select(OccurrenceTaskAnnotation.occurrence_id).where(OccurrenceTaskAnnotation.occurrence_id==QuestionOccurrence.id,
            OccurrenceTaskAnnotation.classification_status=="VERIFIED").exists())) or 0
    return {"occurrences": total, "classified": classified, "unknown": total - classified,
            "pending": total - annotated, "uncertain": annotated - classified,
            "processed": total == annotated, "complete": total == classified,
            "verified":verified,"needs_review":annotated-verified}


def list_questions(session, request: ListRequest, *, signing_key: str,
                   user_id: str = "local", as_of: date | None = None,
                   canonical_ids: tuple[str, ...] | None = None) -> dict:
    started=time.perf_counter()
    state=session.get(CorpusState,1)
    u=session.get(UserRevision,user_id)
    review_revision=u.state_revision if u else 0
    filters=filter_fields(request)
    semantic_scope = None
    if request.semantic_task:
        filters = semantic_base_filters(filters, task=request.semantic_task)
        semantic_scope = load_semantic_scope(session, filters, task=request.semantic_task)
    canonical_scope_hash = None
    if canonical_ids is not None:
        canonical_ids = tuple(sorted(set(canonical_ids)))
        if not semantic_scope or semantic_scope.task != ENGINEERING_DESIGN_TASK:
            raise ValueError("COLLECTION_CATEGORY_REQUIRES_ENGINEERING_DESIGN")
        visible = set(session.scalars(select(QuestionOccurrence.canonical_question_id)
            .select_from(_active_from()).where(*_conditions(filters),
                QuestionOccurrence.id.in_(semantic_scope.selected_occurrence_ids))))
        if not set(canonical_ids) <= visible:
            raise ValueError("INVALID_CATEGORY_SCOPE")
        canonical_scope_hash = hashlib.sha256(json.dumps(canonical_ids,
            separators=(",", ":")).encode()).hexdigest()
    scope=request.model_dump(mode="json",exclude={"cursor"})
    offset,last=0,None
    payload = None
    as_of=as_of or date.today()
    kind="group_list" if isinstance(request,StatsListRequest) and request.group_by!="question" else "question_list"
    if request.cursor:
        payload=verify_scope(request.cursor,key=signing_key,corpus_revision=state.current_revision)
        if payload.get("kind")!=kind or payload.get("scope")!=scope or payload.get("user_id")!=user_id:
            raise ValueError("INVALID_CURSOR_SCOPE")
        if payload.get("user_revision")!=review_revision or payload.get("annotation_revision")!=state.task_annotation_revision:
            raise ValueError("SNAPSHOT_CHANGED")
        if semantic_scope and payload.get("semantic_scope_hash") != semantic_scope.scope_hash:
            raise ValueError("SNAPSHOT_CHANGED")
        if payload.get("canonical_scope_hash") != canonical_scope_hash:
            raise ValueError("INVALID_CURSOR_SCOPE")
        offset=payload.get("offset")
        last=payload.get("last")
        if type(offset) is not int or offset<0 or not isinstance(last,list) or len(last)!=2:
            raise ValueError("INVALID_CURSOR_SCOPE")
        as_of=date.fromisoformat(payload["as_of"])
    # A browsing category partitions the already selected canonical set. Apply
    # Top N/review ordering once to the root, then select a disjoint category;
    # filtering topic occurrences here would lose mixed or pending evidence.
    base_request = request.model_copy(update={"collection_category": None})
    chosen,total=ranked_query(session,base_request,user_id,as_of,semantic_scope=semantic_scope,
        canonical_ids=canonical_ids)
    collection = None
    category = None
    if request.collection_category is not None:
        if request.semantic_task != ENGINEERING_DESIGN_TASK:
            raise ValueError("COLLECTION_CATEGORY_REQUIRES_ENGINEERING_DESIGN")
        if kind != "question_list":
            raise ValueError("COLLECTION_CATEGORY_REQUIRES_QUESTION_LIST")
        root_ids = list(session.scalars(select(chosen.c.key)))
        collection = engineering_collection_categories(session, filters,
            semantic_scope=semantic_scope, canonical_ids=root_ids)
        if payload and payload.get("collection_category_hash") != collection["category_hash"]:
            raise ValueError("SNAPSHOT_CHANGED")
        category = next((item for item in collection["categories"]
            if item["id"] == request.collection_category), None)
        if category is None:
            raise ValueError("INVALID_COLLECTION_CATEGORY")
        chosen = select(*[column for column in chosen.c if column.key != "rank"],
            func.row_number().over(order_by=(chosen.c.rank_score.desc(), chosen.c.key))
                .label("rank")).where(chosen.c.key.in_(category["canonical_ids"])).subquery()
        total = len(category["canonical_ids"])
    result_total=session.scalar(select(func.count()).select_from(chosen)) or 0
    stmt=select(chosen).order_by(chosen.c.rank_score.desc(),chosen.c.key)
    if last:
        stmt=stmt.where(or_(chosen.c.rank_score<last[0],and_(chosen.c.rank_score==last[0],chosen.c.key>last[1])))
    rows=list(session.execute(stmt.limit(request.page_size)).mappings())
    ranked_at=time.perf_counter()
    witnesses={r.id:r for r in session.execute(select(QuestionOccurrence.id,
        QuestionOccurrence.raw_question,SourceDocument.original_relative_path,
        SourceRevision.id.label("source_revision_id"),QuestionOccurrence.source_spans).select_from(_active_from()).where(
            QuestionOccurrence.id.in_([r["witness_id"] for r in rows])))}
    data=[]
    for row in rows:
        item=dict(row)
        witness=witnesses[item.pop("witness_id")]
        item.update(matched_question=witness.raw_question,source_path=witness.original_relative_path,
                    source_summary={"revision_id":witness.source_revision_id,"spans":witness.source_spans})
        item["importance_band"]=importance_band(item["importance_score"])
        item["importance_components"]={"frequency":item.pop("frequency_component"),
            "company_coverage":item.pop("coverage_component"),"recent_frequency":item.pop("recent_component")}
        item.pop("rank_score")
        item.update(response_form=filters.response_form,coding_focus=filters.coding_focus)
        data.append(item)
    if kind=="question_list":
        ids=[x["key"] for x in data]
        texts={q.id:q for q in session.scalars(select(CanonicalQuestion).where(CanonicalQuestion.id.in_(ids)))}
        for item in data:
            q=texts[item["key"]]
            item.update(canonical_question_id=q.id,canonical_text=q.canonical_text,
                        topic_id=q.primary_topic_id,question_type=q.question_type)
    next_offset=offset+len(data)
    common={"kind":kind,"scope":scope,"as_of":as_of.isoformat(),"user_id":user_id,
            "user_revision":review_revision,"annotation_revision":state.task_annotation_revision}
    if semantic_scope:
        common["semantic_scope_hash"] = semantic_scope.scope_hash
    if collection:
        common["collection_category_hash"] = collection["category_hash"]
    if canonical_scope_hash is not None:
        common["canonical_scope_hash"] = canonical_scope_hash
    cursor=sign_scope({**common,"offset":next_offset,"last":[rows[-1]["rank_score"],rows[-1]["key"]]},
        key=signing_key,corpus_revision=state.current_revision) if rows and next_offset<result_total else None
    result_set=sign_scope({**common,"kind":"result_set"},key=signing_key,corpus_revision=state.current_revision)
    enriched_at=time.perf_counter()
    coverage=task_coverage(session,filters)
    sample_conditions = _conditions(filters)
    if semantic_scope:
        sample_conditions.append(QuestionOccurrence.id.in_(semantic_scope.accepted_occurrence_ids))
    if category:
        sample_conditions.append(QuestionOccurrence.canonical_question_id.in_(category["canonical_ids"]))
    if canonical_ids is not None:
        sample_conditions.append(QuestionOccurrence.canonical_question_id.in_(canonical_ids))
    meta={"corpus_revision":state.current_revision,"task_annotation_revision":state.task_annotation_revision,
          "user_state_revision":review_revision,"applied_filters":filters.model_dump(mode="json"),
          "route":"SQL","sort":request.sort,"as_of":as_of.isoformat(),"result_set_id":result_set,
          "review_statuses":request.review_statuses,"review_order":request.review_order,
          "group_by":getattr(request,"group_by","question"),"scoring_versions":dict(SCORING_VERSIONS),
          "sample_counts":{"occurrences":int(session.scalar(select(func.count()).select_from(_active_from()).where(*sample_conditions)) or 0),
                           "selected_occurrences":int(session.scalar(select(func.sum(chosen.c.occurrence_count))) or 0)},
          "list_request":request.model_dump(mode="json", exclude={"cursor"}),
          "pagination":{"total":total,"result_total":result_total,"returned":len(data),"offset":offset,
              "page_size":request.page_size,"top_n":request.top_n,"next_cursor":cursor},"task_coverage":coverage}
    if category:
        meta["collection_category"] = {key: category[key] for key in ("id", "label", "kind", "counts")}
        meta["collection_category"].update(category_hash=collection["category_hash"],
            root_counts=collection["counts"], classification_basis=collection["classification_basis"],
            classification_notice=collection["classification_notice"])
    if semantic_scope:
        meta["semantic_scope"] = semantic_scope.metadata()
        meta["semantic_task"] = request.semantic_task
        for item in data:
            item["semantic_task"] = request.semantic_task
    if semantic_scope and semantic_scope.task == ENGINEERING_DESIGN_TASK:
        accepted_ids = semantic_scope.accepted_occurrence_ids
        pending_ids = semantic_scope.pending_occurrence_ids
        selected_ids = semantic_scope.selected_occurrence_ids
        base_conditions = _conditions(filters)

        confirmed_condition = QuestionOccurrence.id.in_(accepted_ids)
        count_query = select(QuestionOccurrence.canonical_question_id,
            func.sum(case((confirmed_condition, 1), else_=0)).label("confirmed"),
            func.sum(case((confirmed_condition, 0), else_=1)).label("pending"))
        count_source = _active_from()
        count_conditions = [*base_conditions, QuestionOccurrence.id.in_(selected_ids)]
        if category:
            count_conditions.append(QuestionOccurrence.canonical_question_id.in_(category["canonical_ids"]))
        if canonical_ids is not None:
            count_conditions.append(QuestionOccurrence.canonical_question_id.in_(canonical_ids))
        if request.review_statuses:
            count_source = count_source.outerjoin(UserQuestionState, and_(
                UserQuestionState.canonical_question_id == CanonicalQuestion.id,
                UserQuestionState.user_id == user_id))
            count_conditions.append(func.coalesce(UserQuestionState.status, "UNSEEN").in_(request.review_statuses))
        canonical_counts = list(session.execute(count_query.select_from(count_source)
            .where(*count_conditions).group_by(QuestionOccurrence.canonical_question_id)))
        known_ids = {row.canonical_question_id for row in canonical_counts if row.confirmed}
        pending_canonical_ids = {row.canonical_question_id for row in canonical_counts if row.pending}
        meta["semantic_counts"] = {
            "confirmed_canonical_questions": len(known_ids),
            "pending_only_canonical_questions": len(pending_canonical_ids - known_ids),
            "pending_canonical_questions": len(pending_canonical_ids),
            "selected_canonical_questions": len(known_ids | pending_canonical_ids),
            "confirmed_occurrences": sum(row.confirmed for row in canonical_counts),
            "pending_occurrences": sum(row.pending for row in canonical_counts),
            "selected_occurrences": sum(row.confirmed + row.pending for row in canonical_counts)}
        meta["semantic_counts_scope"] = ("selected_collection_category_canonical_questions" if category or canonical_ids is not None
            else "filtered_occurrences_before_top_n")
        pending_by_id = {row["occurrence_id"]: row for row in semantic_scope.pending_records}
        pending_key = _group_key(StatsRequest(**filters.model_dump(), group_by=getattr(request, "group_by", "question"),
            topic_level=getattr(request, "topic_level", "L1")))
        pending_sources = {}
        if pending_ids and data:
            pending_rows = session.execute(select(pending_key.label("group_key"), QuestionOccurrence.id,
                QuestionOccurrence.raw_question, SourceDocument.original_relative_path,
                SourceRevision.id.label("source_revision_id"), QuestionOccurrence.source_spans)
                .select_from(_active_from()).where(*base_conditions,
                    *([QuestionOccurrence.canonical_question_id.in_(canonical_ids)] if canonical_ids is not None else []),
                    QuestionOccurrence.id.in_(pending_ids),
                    pending_key.in_([item["key"] for item in data])).order_by(QuestionOccurrence.id))
            for row in pending_rows:
                pending_sources.setdefault(row.group_key, []).append({**pending_by_id[row.id],
                    "raw_question": row.raw_question, "source_path": row.original_relative_path,
                    "source_summary": {"revision_id": row.source_revision_id, "spans": row.source_spans},
                    "verification": "CURRENT_SQL_SOURCE_PENDING_SEMANTIC_REVIEW"})
        for item in data:
            item["confirmed_occurrence_count"] = item["occurrence_count"]
            item["semantic_verification"] = "CONFIRMED" if item["occurrence_count"] else "PENDING"
            item["pending_sources"] = pending_sources.get(item["key"], [])
        meta["ranking_scope"] = "confirmed_semantic_occurrences_with_pending_visible"
    meta["timings"]={"sql_rank_ms":round((ranked_at-started)*1000,2),
        "sql_enrichment_ms":round((enriched_at-ranked_at)*1000,2),
        "sql_metadata_ms":round((time.perf_counter()-enriched_at)*1000,2)}
    warnings=[]
    if semantic_scope and semantic_scope.pending_occurrence_ids:
        warnings.append(f"当前工程设计范围有 {len(semantic_scope.pending_occurrence_ids)} 次提问待核验，"
            "已与确认结果一起分页展示；待核验项不计入确认题数、频次或排名得分，不能据此声明匹配全集已确认。")
    if (filters.response_form or filters.coding_focus) and not coverage["complete"]:
        missing=[]
        if coverage["pending"]: missing.append(f"{coverage['pending']} 次提问尚未完成任务分类")
        if coverage["uncertain"]: missing.append(f"{coverage['uncertain']} 次提问的任务分类不确定")
        warnings.append("当前范围有 "+"、".join(missing)+"；榜单只包含已明确分类的匹配题目。")
    if request.top_n and total<request.top_n:
        warnings.append(f"当前范围只有 {total} 道匹配题，少于请求的 {request.top_n} 道。")
    if filters.annotation_status=="KNOWN" and coverage["needs_review"]:
        warnings.append("当前使用机器分类；未经完整人工核验，不能将此覆盖率当作分类准确率。")
    if filters.annotation_status=="VERIFIED" and coverage["needs_review"]:
        warnings.append(f"仅显示已核验分类；当前范围另有 {coverage['needs_review']} 次提问的分类待核验。")
    return {"data":data,"meta":meta,"warnings":warnings}
