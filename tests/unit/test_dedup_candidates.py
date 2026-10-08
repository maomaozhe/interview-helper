from types import SimpleNamespace

import httpx
import pytest

from interview_intelligence.dedup.candidates import ElasticsearchCandidateHead, exact_rank, text_hash


def item(key, text): return SimpleNamespace(id=key, canonical_text=text)


def test_exact_cosine_ties_dimensions_and_invalid_vectors():
    items = [item("b","B"),item("a","A"),item("c","C")]
    ranked = exact_rank([1,0],items,[[1,0],[2,0],[0,1]],2)
    assert [x.id for _,x in ranked] == ["a","b"]
    for query, vectors in (([0,0],[[1,0]]),([1,0],[[float("nan"),0]]),([1,0],[[1,0,0]])):
        with pytest.raises(ValueError, match="INVALID_CANDIDATE_VECTOR"):
            exact_rank(query,[items[0]],vectors,1)


def test_ann_keeps_unindexed_changed_and_new_model_items_in_exact_delta():
    head = ElasticsearchCandidateHead("http://fixture")
    head.physical = "immutable-index"
    head.identities = {"a":(text_hash("unchanged"),"v1"),"b":(text_hash("old"),"v1"),
                       "c":(text_hash("same text"),"old-model"),"deleted":(text_hash("gone"),"v1")}
    head.refresh = lambda: None
    requests = []
    def request(method,path,**kwargs):
        requests.append((method,path,kwargs))
        return {"hits":{"hits":[{"_id":"a"}]}}
    head.es._request = request
    rows = [item("a","unchanged"),item("b","new"),item("c","same text"),item("d","uncommitted")]
    result, meta = head.propose([1,0],rows,"v1",10)
    assert [x.id for x in result] == ["a","b","c","d"]
    assert meta["delta_count"] == 3 and meta["indexed_count"] == 1
    assert requests[0][1] == "/immutable-index/_search"
    assert requests[0][2]["json"]["knn"]["filter"]["terms"]["_id"] == ["a"]
    # A later transaction after rollback uses only its own visible canonicals.
    result, meta = head.propose([1,0],rows[:1],"v1",10)
    assert [x.id for x in result] == ["a"] and meta["delta_count"] == 0


def test_ann_rejects_out_of_scope_or_duplicate_ids():
    head = ElasticsearchCandidateHead("http://fixture")
    head.physical = "immutable-index"; head.refresh = lambda: None
    head.identities = {"a":(text_hash("A"),"v1")}
    for ids in (["a","a"],["unknown"]):
        head.es._request = lambda *a,**k: {"hits":{"hits":[{"_id":key} for key in ids]}}
        with pytest.raises(ValueError,match="ANN_CANDIDATE_SCOPE_INVALID"):
            head.propose([1,0],[item("a","A")],"v1",10)


def test_head_refresh_closes_pit_and_does_not_cache_failed_scan():
    head = ElasticsearchCandidateHead("http://fixture")
    requests = []
    def request(method,path,**kwargs):
        requests.append((method,path))
        if path.startswith("/_alias"): return {"next-index":{}}
        if path.endswith("/_pit"): return {"id":"pit-1"}
        if path=="/_search": raise httpx.ConnectError("offline")
        return {"succeeded":True}
    head.es._request = request
    with pytest.raises(httpx.ConnectError): head.refresh()
    assert requests[-1] == ("delete","/_pit")
    assert head.physical is None and head.identities == {}
