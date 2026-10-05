"""Set semantics, score/keyset equivalence, preferences and crash-safe writes."""
import asyncio
import time
from datetime import timedelta
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select,func
from interview_intelligence.api import create_app
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.agent.query_contract import QueryRequest,QuerySpec
from interview_intelligence.agent.preferences import PreferenceService,PreferenceUpdate
from interview_intelligence.analytics.listing import ListRequest,StatsListRequest,list_questions
from interview_intelligence.analytics.stats import query_question_stats
from interview_intelligence.contracts import ReviewRequest,StatsRequest
from interview_intelligence.domain.models import AgentTurn,AgentEvent,ReviewEvent,now_utc
from interview_intelligence.review.service import ReviewService
from test_query_agent import ranked_corpus
from test_analytics import seed_corpus
from interview_intelligence.agent.annotation_review import AnnotationReviewer,AnnotationReview,AnnotationPublish
from interview_intelligence.domain.models import OccurrenceTaskAnnotation,CorpusState,TaskAnnotationDraft,QuestionOccurrence

def test_machine_labels_require_review_and_publication_is_atomic_and_revision_bound():
    db,_=ranked_corpus()
    with db.session() as s,s.begin():
        annotations=list(s.scalars(select(OccurrenceTaskAnnotation).limit(2)))
        ids=[a.occurrence_id for a in annotations]
        for a in annotations: a.classification_status="NEEDS_REVIEW"
    reviewer=AnnotationReviewer(db,"reviewer","test")
    queue=reviewer.queue(limit=1)
    assert queue["next_cursor"]
    assert len(reviewer.queue(queue["next_cursor"],1)["items"])==1
    rev=queue["annotation_revision"]
    for i in ids:
        reviewer.stage(i,AnnotationReview(response_form="CODE",coding_focus="MIXED",reason="fixture human decision",
                                          expected_annotation_revision=rev))
    # A concurrent publication invalidates this entire batch before any label is changed.
    with db.session() as s,s.begin(): s.get(CorpusState,1).task_annotation_revision+=1
    with pytest.raises(ValueError,match="SNAPSHOT_CHANGED"):
        reviewer.publish(AnnotationPublish(occurrence_ids=ids,expected_annotation_revision=rev))
    with db.session() as s:
        assert all(s.get(OccurrenceTaskAnnotation,i).classification_status=="NEEDS_REVIEW" for i in ids)
        assert all(s.get(TaskAnnotationDraft,i) for i in ids)
    result=reviewer.publish(AnnotationPublish(occurrence_ids=ids,expected_annotation_revision=rev+1))
    assert result["annotation_revision"]==rev+2
    with db.session() as s:
        for i in ids:
            a=s.get(OccurrenceTaskAnnotation,i)
            assert a.classification_status=="VERIFIED" and a.evidence["reviewer_id"]=="reviewer"
            assert s.get(TaskAnnotationDraft,i) is None
        for focus in ["ENGINEERING","ALGORITHM"]:
            found=list_questions(s,ListRequest(coding_focus=focus,annotation_status="VERIFIED",page_size=100),signing_key="test")
            assert {s.get(QuestionOccurrence,i).canonical_question_id for i in ids}<={r["key"] for r in found["data"]}

def test_group_stats_reject_question_only_review_semantics():
    with pytest.raises(ValueError): StatsListRequest(group_by="company",review_statuses=["UNSEEN"])
    with pytest.raises(ValueError): StatsListRequest(group_by="company",sort="gap")

def test_resume_event_cursor_skips_old_failure_and_preserves_completed_read_context():
    db,_=ranked_corpus()
    service=QueryService(db,signing_key="test",task_annotation_policy="KNOWN")
    request=QueryRequest(message="先列题再查复习状态",request_id="read-checkpoint")
    run=service.begin(request)
    first=service.execute(run,"list_questions",QuerySpec(action="LIST",page_size=3,final=False))
    service.fail(run,ValueError("simulated interruption"))
    resumed=QueryService(db,signing_key="test",task_annotation_policy="KNOWN")
    run=resumed.begin(request)
    assert run.state["current_page_ids"]==[r["key"] for r in first["facts"]["data"]]
    assert resumed.model_context(run)["completed_tools"][0]["intent"]=="LIST"
    view=resumed.journal.view(run.id)
    fresh=resumed.journal.view(run.id,view["event_cursor"])
    assert [e["type"] for e in fresh["events"]]==["accepted"]
    resumed.execute(run,"get_review_state",QuerySpec(action="REVIEW_STATE",question_ids=run.state["current_page_ids"]))
    result=resumed.finish(run,provider="fixture")
    assert result["facts"]["components"][0]["intent"]=="LIST"

def pages(db,req):
    result=[]
    with db.session() as s:
        while True:
            p=list_questions(s,req,signing_key="test")
            result.extend(p["data"])
            if not p["meta"]["pagination"]["next_cursor"]: return result
            req=req.model_copy(update={"cursor":p["meta"]["pagination"]["next_cursor"]})

def test_review_before_and_after_top40_are_different_sets():
    db,expected=ranked_corpus()
    ReviewService(db).record("local",ReviewRequest(idempotency_key="master-two",items=[
        {"canonical_question_id":i,"status":"MASTERED"} for i,_ in expected[:2]]))
    req=ListRequest(coding_focus="ALGORITHM",top_n=40,page_size=7,review_statuses=["UNSEEN"])
    assert [r["key"] for r in pages(db,req)]==[i for i,_ in expected[2:42]]
    assert [r["key"] for r in pages(db,req.model_copy(update={"review_order":"AFTER_TOP_N"}))]==[i for i,_ in expected[2:40]]

@pytest.mark.parametrize("sort",["importance","gap"])
def test_sql_score_ranking_and_keysets_equal_independent_legacy_formula(sort):
    db,_=ranked_corpus()
    with db.session() as s:
        expected=query_question_stats(s,StatsRequest(coding_focus="ALGORITHM",sort=sort,limit=100))["data"]
    actual=pages(db,ListRequest(coding_focus="ALGORITHM",sort=sort,page_size=7))
    assert [r["key"] for r in actual]==[r["key"] for r in expected]
    assert [r["importance_score"] for r in actual]==pytest.approx([r["importance_score"] for r in expected])

def test_grouped_stats_cursor_is_complete_and_bound_to_grouping():
    db,_,_=seed_corpus()
    req=StatsListRequest(group_by="company",page_size=1)
    actual=pages(db,req)
    with db.session() as s:
        expected=query_question_stats(s,StatsRequest(group_by="company",limit=100))["data"]
        first=list_questions(s,req,signing_key="test")
        with pytest.raises(ValueError,match="INVALID_CURSOR_SCOPE"):
            list_questions(s,req.model_copy(update={"group_by":"topic","cursor":first["meta"]["pagination"]["next_cursor"]}),signing_key="test")
    assert [r["key"] for r in actual]==[r["key"] for r in expected]

def test_preference_versions_expiry_delete_and_temporary_override_context():
    db,_,_=seed_corpus()
    p=PreferenceService(db,"local")
    saved=p.update("coding_focus",PreferenceUpdate(value="ALGORITHM",expected_version=0,source_message="以后默认看算法"))
    with pytest.raises(ValueError,match="PREFERENCE_VERSION_CONFLICT"):
        p.update("coding_focus",PreferenceUpdate(value="ENGINEERING",expected_version=0,source_message="过期修改"))
    service=QueryService(db,signing_key="test")
    run=service.begin(QueryRequest(message="这次看工程代码",request_id="temporary",
        filters={"coding_focus":"ENGINEERING"}))
    c=service.model_context(run)
    assert c["preferences"]["coding_focus"]=="ALGORITHM"
    assert c["explicit_filters"]["coding_focus"]=="ENGINEERING"
    service.fail(run,ValueError("QUERY_CANCELLED"))
    assert p.defaults()["coding_focus"]=="ALGORITHM"
    p.delete("coding_focus",saved["version"])
    assert p.defaults()=={} and p.get("coding_focus")["version"]==2
    assert PreferenceService(db,"other").defaults()=={}
    p.update("page_size",PreferenceUpdate(value=40,expected_version=0,source_message="40条",
        expires_at=now_utc()+timedelta(seconds=1)))
    with pytest.raises(ValueError,match="INVALID_PREFERENCE_VALUE"):
        p.update("page_size",PreferenceUpdate(value=True,expected_version=1,source_message="错误"))

@pytest.mark.parametrize("commit_result",[False,True])
def test_restart_recovers_review_intention_without_replanning_or_duplicate_events(commit_result):
    db,expected=ranked_corpus()
    service=QueryService(db,signing_key="test")
    first=service.begin(QueryRequest(message="算法",request_id="page"))
    service.execute(first,"list_questions",QuerySpec(action="LIST",filters={"coding_focus":"ALGORITHM"},page_size=40))
    service.finish(first,provider="fixture")
    body=QueryRequest(message="这些已掌握",request_id="write",conversation_id=first.conversation_id,expected_version=1)
    run=service.begin(body)
    plan=QuerySpec(action="RECORD_REVIEW",filters={"coding_focus":"ALGORITHM"},review_items=[
        {"canonical_question_id":i,"status":"MASTERED","expected_version":0} for i,_ in expected[:40]])
    original=service.journal.complete
    if not commit_result:
        def crash(*args): raise RuntimeError("crash after business commit")
        service.journal.complete=crash
        with pytest.raises(RuntimeError): service.execute(run,"record_review",plan)
    else: service.execute(run,"record_review",plan)
    service.journal.complete=original
    service.fail(run,RuntimeError("process stopped before final receipt"))
    class NoPlanner:
        def plan(self,*args): raise AssertionError("a saved write must not be replanned")
    with TestClient(create_app(db,Settings(),query_planner=NoPlanner())) as client:
        response=client.post("/api/questions/query",json=body.model_dump(mode="json"))
        assert response.status_code==200,response.text
        assert response.json()["meta"]["planning"]["provider"] in {"recovered_receipt","recovered_write"}
        assert client.post("/api/questions/query",json=body.model_dump(mode="json")).json()==response.json()
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(ReviewEvent))==40

from interview_intelligence.config import Settings

def test_async_accept_duplicate_cancel_and_owner_bound_receipts():
    db,_,_=seed_corpus()
    class SlowPlanner:
        async def plan(self,run,context):
            await asyncio.sleep(5)
            return QuerySpec(action="LIST")
    app=create_app(db,Settings(),query_planner=SlowPlanner())
    with TestClient(app) as c:
        conv=c.post("/api/conversations").json()["data"]["conversation_id"]
        body={"message":"高频题","request_id":"async","conversation_id":conv,"expected_version":0}
        accepted=c.post(f"/api/conversations/{conv}/messages",json=body)
        assert accepted.status_code==202
        run_id=accepted.json()["data"]["run_id"]
        repeated=c.post(f"/api/conversations/{conv}/messages",json=body)
        assert repeated.status_code==202 and repeated.json()["data"]["run_id"]==run_id
        assert c.get("/api/query/receipts/async").json()["data"]["run_id"]==run_id
        c.post(f"/api/runs/{run_id}/cancel")
        until=time.monotonic()+2
        while time.monotonic()<until:
            state=c.get(f"/api/runs/{run_id}").json()["data"]
            if state["status"]!="RUNNING":break
            time.sleep(.01)
        assert state["status"]=="CANCELLED",state
        assert c.get(f"/api/runs/{run_id}/events").status_code==200
    foreign=create_app(db,Settings(local_user_id="someone-else"))
    with TestClient(foreign) as c: assert c.get(f"/api/runs/{run_id}").status_code==404

def test_expired_lease_is_interrupted_and_event_replay_is_ordered():
    db,_,_=seed_corpus()
    service=QueryService(db,signing_key="test")
    run=service.begin(QueryRequest(message="高频题",request_id="crashed"))
    with db.session() as s,s.begin():
        s.get(AgentTurn,run.id).lease_until=now_utc()-timedelta(seconds=1)
    recovered=QueryService(db,signing_key="test")
    view=recovered.journal.view(run.id)
    assert view["status"]=="INTERRUPTED"
    assert [e["type"] for e in view["events"]]==["accepted","interrupted"]
    assert recovered.journal.view(run.id,after=1)["events"][0]["sequence"]==2
