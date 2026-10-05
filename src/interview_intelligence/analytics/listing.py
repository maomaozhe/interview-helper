"""Exact SQL category lists and global ranking with revision-bound pagination."""
from __future__ import annotations

from datetime import date
import time
from typing import Literal

from pydantic import Field, model_validator
from sqlalchemy import distinct, func, select, case, and_, or_

from interview_intelligence.analytics.scope import sign_scope, verify_scope
from interview_intelligence.analytics.stats import _active_from, _conditions, _effective_date, _group_key, subtract_calendar_months
from interview_intelligence.contracts import FilterSpec, StatsRequest
from interview_intelligence.domain.models import (
    CanonicalQuestion, CorpusState, Interview, OccurrenceTaskAnnotation,
    QuestionOccurrence, SourceDocument, SourceRevision, UserQuestionState, UserRevision,
)


class ListRequest(FilterSpec):
    sort: Literal["frequency", "importance", "gap"] = "frequency"
    top_n: int | None = Field(default=None, ge=1, le=1000)
    page_size: int = Field(default=20, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=4096)
    review_statuses: list[Literal["UNSEEN","WEAK","REVIEWED","MASTERED"]] = Field(default_factory=list,max_length=4)
    review_order: Literal["BEFORE_TOP_N","AFTER_TOP_N"] = "BEFORE_TOP_N"


class StatsListRequest(ListRequest):
    group_by: Literal["question","topic","company","round"] = "question"
    topic_level: Literal["L1","L2"] = "L1"

    @model_validator(mode="after")
    def validate_group_scope(self):
        if self.group_by != "question" and (self.review_statuses or self.sort != "frequency"):
            raise ValueError("Group statistics support frequency sorting without question review filters")
        return self


def filter_fields(request):
    return FilterSpec.model_validate({k:getattr(request,k) for k in FilterSpec.model_fields})


def ranked_query(session, request, user_id, as_of):
    """All aggregation, score normalization, Top N and review set order live in SQL."""
    grouped_by = getattr(request,"group_by","question")
    f = filter_fields(request)
    key = _group_key(StatsRequest(**f.model_dump(),group_by=grouped_by,
        topic_level=getattr(request,"topic_level","L1")))
    effective = _effective_date(f.date_basis)
    recent = and_(effective>=subtract_calendar_months(as_of,3),effective<as_of.fromordinal(as_of.toordinal()+1))
    g = select(key.label("key"),func.count().label("occurrence_count"),
        func.count(distinct(Interview.id)).label("interview_count"),
        func.count(distinct(SourceDocument.id)).label("source_document_count"),
        func.count(distinct(Interview.company_normalized)).label("company_count"),
        func.sum(case((recent,1),else_=0)).label("recent_count"),
        func.min(QuestionOccurrence.id).label("witness_id")
    ).select_from(_active_from()).where(*_conditions(f)).group_by(key).subquery()
    known = session.scalar(select(func.count(distinct(Interview.company_normalized))).select_from(
        _active_from()).where(*_conditions(f))) or 0
    freq = func.coalesce(func.ln(g.c.occurrence_count+1.0)/func.nullif(func.ln(func.max(g.c.occurrence_count).over()+1.0),0),0.0)
    recent_score = func.coalesce(g.c.recent_count*1.0/func.nullif(func.max(g.c.recent_count).over(),0),0.0)
    coverage = g.c.company_count*1.0/max(1,known)
    importance = .5*freq+.3*coverage+.2*recent_score
    status = func.coalesce(UserQuestionState.status,"UNSEEN") if grouped_by=="question" else "UNSEEN"
    base = select(g,importance.label("importance_score"),freq.label("frequency_component"),
        coverage.label("coverage_component"),recent_score.label("recent_component"))
    if grouped_by=="question":
        base=base.add_columns(status.label("user_status")).outerjoin(UserQuestionState,
            and_(UserQuestionState.canonical_question_id==g.c.key,UserQuestionState.user_id==user_id))
    else:
        from sqlalchemy import literal
        base=base.add_columns(literal("UNSEEN").label("user_status"))
    b=base.subquery()
    gap=b.c.importance_score*case((b.c.user_status=="WEAK",1.0),(b.c.user_status=="UNSEEN",.8),
        (b.c.user_status=="REVIEWED",.4),else_=0.0)
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
                   user_id: str = "local", as_of: date | None = None) -> dict:
    started=time.perf_counter()
    state=session.get(CorpusState,1)
    u=session.get(UserRevision,user_id)
    review_revision=u.state_revision if u else 0
    filters=filter_fields(request)
    scope=request.model_dump(mode="json",exclude={"cursor"})
    offset,last=0,None
    as_of=as_of or date.today()
    kind="group_list" if isinstance(request,StatsListRequest) and request.group_by!="question" else "question_list"
    if request.cursor:
        payload=verify_scope(request.cursor,key=signing_key,corpus_revision=state.current_revision)
        if payload.get("kind")!=kind or payload.get("scope")!=scope or payload.get("user_id")!=user_id:
            raise ValueError("INVALID_CURSOR_SCOPE")
        if payload.get("user_revision")!=review_revision or payload.get("annotation_revision")!=state.task_annotation_revision:
            raise ValueError("SNAPSHOT_CHANGED")
        offset=payload.get("offset")
        last=payload.get("last")
        if type(offset) is not int or offset<0 or not isinstance(last,list) or len(last)!=2:
            raise ValueError("INVALID_CURSOR_SCOPE")
        as_of=date.fromisoformat(payload["as_of"])
    chosen,total=ranked_query(session,request,user_id,as_of)
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
        item["importance_band"]="CORE" if item["importance_score"]>=.7 else "COMMON" if item["importance_score"]>=.3 else "LONG_TAIL"
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
    cursor=sign_scope({**common,"offset":next_offset,"last":[rows[-1]["rank_score"],rows[-1]["key"]]},
        key=signing_key,corpus_revision=state.current_revision) if rows and next_offset<result_total else None
    result_set=sign_scope({**common,"kind":"result_set"},key=signing_key,corpus_revision=state.current_revision)
    enriched_at=time.perf_counter()
    coverage=task_coverage(session,filters)
    meta={"corpus_revision":state.current_revision,"task_annotation_revision":state.task_annotation_revision,
          "user_state_revision":review_revision,"applied_filters":filters.model_dump(mode="json"),
          "route":"SQL","sort":request.sort,"as_of":as_of.isoformat(),"result_set_id":result_set,
          "review_statuses":request.review_statuses,"review_order":request.review_order,
          "group_by":getattr(request,"group_by","question"),"scoring_versions":{"importance":"importance_v1","gap":"gap_v1"},
          "sample_counts":{"occurrences":int(session.scalar(select(func.count()).select_from(_active_from()).where(*_conditions(filters))) or 0),
                           "selected_occurrences":int(session.scalar(select(func.sum(chosen.c.occurrence_count))) or 0)},
          "pagination":{"total":total,"result_total":result_total,"returned":len(data),"offset":offset,
              "page_size":request.page_size,"top_n":request.top_n,"next_cursor":cursor},"task_coverage":coverage}
    meta["timings"]={"sql_rank_ms":round((ranked_at-started)*1000,2),
        "sql_enrichment_ms":round((enriched_at-ranked_at)*1000,2),
        "sql_metadata_ms":round((time.perf_counter()-enriched_at)*1000,2)}
    warnings=[]
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
