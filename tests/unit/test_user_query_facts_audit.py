"""Safety checks for the independent post-replay auditor, using no live service."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from eval.common import digest
from evals.agent_gold import user_query_facts_audit as audit


def facts():
    return {**{name: [{"id": name + "-1", "value": "original", "nested": {"label": "KNOWN"}}]
               for name in audit.COUNTS}, "snapshot": audit.SNAPSHOT,
            "followups": [{"source_id": "a", "target_id": "b", "evidence_spans": [{"start_char": 10}]}]}


def test_full_rows_detect_mutations_even_when_counts_and_ids_stay_the_same():
    expected = facts()
    actual = deepcopy(expected)
    actual["questions"][0]["nested"]["label"] = "CHANGED"
    result = audit.compare_facts(expected, actual)
    assert not result["match"]
    question = result["tables"]["questions"]
    assert question["count"] == question["current_count"] == 1
    assert question["changed_rows"][0]["changed_fields"] == ["nested"]
    assert "CHANGED" not in json.dumps(result)


def test_comparison_ignores_row_order_but_preserves_relation_multiplicity_and_evidence():
    expected = facts()
    expected["questions"].append({"id": "a", "value": 2})
    actual = deepcopy(expected)
    actual["questions"].reverse()
    assert audit.compare_facts(expected, actual)["match"]
    actual["followups"].append(deepcopy(actual["followups"][0]))
    assert not audit.compare_facts(expected, actual)["followups"]["match"]
    actual = deepcopy(expected)
    actual["followups"][0]["evidence_spans"][0]["start_char"] = 11
    assert not audit.compare_facts(expected, actual)["followups"]["match"]


def test_duplicate_ids_cannot_hide_a_removed_fact():
    with pytest.raises(audit.AuditError, match="DUPLICATE_CORE_ROW_ID"):
        audit.compare_table([{"id": "a"}], [{"id": "a"}, {"id": "a"}])


def test_source_hashes_are_read_from_bytes_and_missing_files_fail(tmp_path):
    raw = b"# real archived source\r\n"
    source_hash = digest(raw)
    (tmp_path / (source_hash + ".md")).write_bytes(raw)
    docs = [{"id": "doc1", "source_hash": source_hash}]
    assert audit.verify_sources(docs, tmp_path, exported=False)["match"]
    (tmp_path / (source_hash + ".md")).write_bytes(raw.replace(b"\r\n", b"\n"))
    assert audit.verify_sources(docs, tmp_path, exported=False)["failures"][0]["reason"] == "HASH_MISMATCH"
    (tmp_path / (source_hash + ".md")).unlink()
    assert audit.verify_sources(docs, tmp_path, exported=False)["failures"][0]["reason"] == "MISSING"


def test_wrong_or_normal_actor_is_rejected_before_database_access():
    seal = {"actor": "eval-audit", "as_of": audit.SNAPSHOT["as_of"]}
    settings = SimpleNamespace(local_user_id="normal-user", multi_tenant_enabled=False)
    with pytest.raises(audit.AuditError, match="ACTOR_SETTINGS_MISMATCH"):
        audit.check_actor("eval-audit", seal, settings)
    with pytest.raises(audit.AuditError, match="EVALUATION_ACTOR_REQUIRED"):
        audit.check_actor("normal-user", seal, settings)


def test_read_only_session_rejects_accidental_sql_write_and_leaves_database_unchanged(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "readonly.db"), poolclass=NullPool)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sentinel (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO sentinel VALUES (1)"))
    with audit.readonly_session(engine) as session:
        assert session.scalar(text("SELECT count(*) FROM sentinel")) == 1
        with pytest.raises(OperationalError, match="readonly"):
            session.execute(text("INSERT INTO sentinel VALUES (2)"))
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM sentinel")) == 1
    engine.dispose()


def test_cli_does_not_emit_driver_or_config_exception_secrets(tmp_path, monkeypatch, capsys):
    output = tmp_path / "safe-error.json"
    monkeypatch.setattr("sys.argv", ["audit", "--dataset", "unused", "--runtime-seal", "unused",
                                     "--output", str(output)])
    def unsafe_driver_error(_args):
        raise RuntimeError("driver URL includes PASSWORD_SHOULD_NEVER_APPEAR")
    monkeypatch.setattr(audit, "audit", unsafe_driver_error)
    with pytest.raises(SystemExit) as exit_info:
        audit.main()
    assert exit_info.value.code == 1
    assert "PASSWORD_SHOULD_NEVER_APPEAR" not in output.read_text(encoding="utf-8") + capsys.readouterr().out
    assert json.loads(output.read_text(encoding="utf-8"))["error_code"] == "AUDIT_FAILED_RuntimeError"
