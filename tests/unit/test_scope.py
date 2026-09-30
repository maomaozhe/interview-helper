import pytest

from interview_intelligence.analytics.scope import sign_scope, verify_scope


def test_signed_scope_rejects_tampering_and_revision_change():
    token = sign_scope({"group_by": "question", "key": "q1", "filters": {}},
                       key="secret", corpus_revision=3, issued_at=1000)
    assert verify_scope(token, key="secret", corpus_revision=3, now=1100)["key"] == "q1"
    with pytest.raises(ValueError, match="SNAPSHOT_CHANGED"):
        verify_scope(token, key="secret", corpus_revision=4, now=1100)
    with pytest.raises(ValueError, match="INVALID_SCOPE"):
        verify_scope(token + "x", key="secret", corpus_revision=3, now=1100)
