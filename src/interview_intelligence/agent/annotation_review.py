"""Explicit label review, staged and atomically published with source/version checks."""
import hashlib,json
from typing import Literal
from pydantic import Field
from sqlalchemy import select
from interview_intelligence.contracts import StrictModel,FilterSpec
from interview_intelligence.analytics.stats import _active_from,_conditions
from interview_intelligence.analytics.scope import sign_scope,verify_scope
from interview_intelligence.domain.models import (
    QuestionOccurrence,SourceRevision,SourceDocument,OccurrenceTaskAnnotation,
    TaskAnnotationDraft,CorpusState,now_utc)

class AnnotationReview(StrictModel):
    response_form: Literal["CODE","SQL","VERBAL","EXPLANATION","DESIGN","OTHER","UNKNOWN"]
    coding_focus: Literal["ENGINEERING","ALGORITHM","MIXED","NONE","UNKNOWN"]
    reason: str=Field(min_length=1,max_length=1000)
    expected_annotation_revision: int=Field(ge=0)

class AnnotationPublish(StrictModel):
    occurrence_ids: list[str]=Field(min_length=1,max_length=40)
    expected_annotation_revision: int=Field(ge=0)

class AnnotationReviewer:
    def __init__(self,database,user_id,signing_key):
        self.database,self.user_id,self.signing_key=database,user_id,signing_key
    def _source(self,s,occurrence_id):
        row=s.execute(select(QuestionOccurrence,SourceRevision,SourceDocument).select_from(_active_from()).where(
            *_conditions(FilterSpec()),QuestionOccurrence.id==occurrence_id)).first()
        if not row: raise KeyError("ACTIVE_OCCURRENCE_NOT_FOUND")
        o,r,d=row
        basis={"source_revision_id":r.id,"raw_file_hash":r.raw_file_hash,"question":o.raw_question,"spans":o.source_spans}
        fingerprint=hashlib.sha256(json.dumps(basis,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        return o,r,d,basis,fingerprint
    def queue(self,cursor=None,limit=20):
        if not 1<=limit<=50: raise ValueError("INVALID_PAGE_SIZE")
        with self.database.session() as s:
            state=s.get(CorpusState,1)
            after=""
            if cursor:
                p=verify_scope(cursor,key=self.signing_key,corpus_revision=state.current_revision)
                if p.get("kind")!="annotation_queue" or p.get("user_id")!=self.user_id or p.get("limit")!=limit:
                    raise ValueError("INVALID_CURSOR_SCOPE")
                if p.get("annotation_revision")!=state.task_annotation_revision: raise ValueError("SNAPSHOT_CHANGED")
                after=p["after"]
            ids=list(s.scalars(select(QuestionOccurrence.id).select_from(_active_from()).where(
                *_conditions(FilterSpec()),QuestionOccurrence.id>after,
                ~select(OccurrenceTaskAnnotation.occurrence_id).where(
                    OccurrenceTaskAnnotation.occurrence_id==QuestionOccurrence.id,
                    OccurrenceTaskAnnotation.classification_status=="VERIFIED").exists())
                .order_by(QuestionOccurrence.id).limit(limit+1)))
            rows=[]
            for i in ids[:limit]:
                o,r,d,basis,fingerprint=self._source(s,i)
                a=s.get(OccurrenceTaskAnnotation,i)
                draft=s.get(TaskAnnotationDraft,i)
                rows.append({"occurrence_id":i,"raw_question":o.raw_question,"source_path":d.original_relative_path,
                    "source_revision_id":r.id,"source_spans":o.source_spans,
                    "response_form":a.response_form if a else "UNKNOWN","coding_focus":a.coding_focus if a else "UNKNOWN",
                    "classification_status":a.classification_status if a else "UNKNOWN",
                    "draft":draft.payload if draft and draft.reviewer_id==self.user_id else None})
            next_cursor=sign_scope({"kind":"annotation_queue","user_id":self.user_id,"limit":limit,
                "annotation_revision":state.task_annotation_revision,"after":ids[limit-1]},
                key=self.signing_key,corpus_revision=state.current_revision) if len(ids)>limit else None
            return {"items":rows,"annotation_revision":state.task_annotation_revision,"next_cursor":next_cursor}
    def stage(self,occurrence_id,request):
        with self.database.session() as s,s.begin():
            state=s.get(CorpusState,1,with_for_update=True)
            if state.task_annotation_revision!=request.expected_annotation_revision: raise ValueError("SNAPSHOT_CHANGED")
            o,r,d,basis,fingerprint=self._source(s,occurrence_id)
            draft=s.get(TaskAnnotationDraft,occurrence_id)
            if draft and draft.reviewer_id!=self.user_id: raise ValueError("ANNOTATION_REVIEW_CONFLICT")
            if not draft:
                draft=TaskAnnotationDraft(occurrence_id=occurrence_id,reviewer_id=self.user_id,payload={},evidence={})
                s.add(draft)
            draft.payload=request.model_dump(mode="json")
            draft.evidence={**basis,"fingerprint":fingerprint}
            return {"occurrence_id":occurrence_id,"staged":True,"annotation_revision":state.task_annotation_revision}
    def publish(self,request):
        if len(set(request.occurrence_ids))!=len(request.occurrence_ids): raise ValueError("DUPLICATE_ANNOTATION")
        with self.database.session() as s,s.begin():
            state=s.get(CorpusState,1,with_for_update=True)
            if state.task_annotation_revision!=request.expected_annotation_revision: raise ValueError("SNAPSHOT_CHANGED")
            for i in request.occurrence_ids:
                draft=s.get(TaskAnnotationDraft,i,with_for_update=True)
                if not draft or draft.reviewer_id!=self.user_id: raise KeyError("ANNOTATION_DRAFT_NOT_FOUND")
                try: source=self._source(s,i)
                except KeyError: raise ValueError("SNAPSHOT_CHANGED") from None
                if source[-1]!=draft.evidence["fingerprint"]: raise ValueError("SNAPSHOT_CHANGED")
                a=s.get(OccurrenceTaskAnnotation,i)
                if not a:
                    a=OccurrenceTaskAnnotation(occurrence_id=i,response_form="UNKNOWN",coding_focus="UNKNOWN",
                        producer_version="human-task-v1",evidence={})
                    s.add(a)
                previous={"response_form":a.response_form,"coding_focus":a.coding_focus,"producer_version":a.producer_version,
                          "classification_status":a.classification_status}
                a.response_form,a.coding_focus=draft.payload["response_form"],draft.payload["coding_focus"]
                a.classification_status="UNKNOWN" if "UNKNOWN" in {a.response_form,a.coding_focus} else "VERIFIED"
                a.producer_version="human-task-v1"
                a.evidence={**draft.evidence,"reviewer_id":self.user_id,"reason":draft.payload["reason"],
                    "reviewed_at":now_utc().isoformat(),"previous":previous,"original_evidence":a.evidence.get("original_evidence",a.evidence)}
                s.delete(draft)
            state.task_annotation_revision+=1
            return {"published":len(request.occurrence_ids),"annotation_revision":state.task_annotation_revision}
