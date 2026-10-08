"""File-lock retries publish complete receipts and never repeat HTTP work."""
import json
from pathlib import Path
import sys

import httpx
import pytest

from scripts import evaluate_conversation_quality as conversation
from scripts import evaluate_historical_requery as historical
from scripts import receipt_io


def test_atomic_update_retries_permission_error_with_complete_unique_temporary(tmp_path, monkeypatch):
    target = tmp_path / "receipt.json"
    original = {"status": "RUNNING", "turns": []}
    target.write_text(json.dumps(original), encoding="utf-8")
    updated = {"status": "SUCCEEDED", "turns": [{"message": "字节 Redis"}]}
    replace = receipt_io.os.replace
    attempts = []
    sleeps = []

    def locked_once(source, destination):
        source = Path(source)
        attempts.append(source)
        assert source.parent == target.parent and source != target
        assert json.loads(source.read_text(encoding="utf-8")) == updated
        assert json.loads(target.read_text(encoding="utf-8")) == original
        if len(attempts) == 1:
            raise PermissionError("scanner holds destination")
        replace(source, destination)

    monkeypatch.setattr(receipt_io.os, "replace", locked_once)
    monkeypatch.setattr(receipt_io.time, "sleep", sleeps.append)
    receipt_io.atomic_write_json(target, updated)
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert sleeps == [0.05]
    assert json.loads(target.read_text(encoding="utf-8")) == updated
    assert list(tmp_path.iterdir()) == [target]


def test_permanent_permission_error_is_bounded_and_preserves_previous_bytes(tmp_path, monkeypatch):
    target = tmp_path / "receipt.json"
    previous = b'{"status":"FAILED","turns":[{"run_id":"old-evidence"}]}'
    target.write_bytes(previous)
    clock = [0.0]
    attempts = []

    def locked(source, destination):
        attempts.append(source)
        assert target.read_bytes() == previous
        raise PermissionError("destination remains locked")

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(receipt_io.os, "replace", locked)
    monkeypatch.setattr(receipt_io.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(receipt_io.time, "sleep", sleep)
    with pytest.raises(PermissionError, match="remains locked"):
        receipt_io.atomic_write_json(target, {"status": "SUCCEEDED"})
    assert len(attempts) > 1 and clock[0] == pytest.approx(3.0)
    assert target.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [target]


def test_initial_publish_refuses_existing_complete_receipt(tmp_path):
    target = tmp_path / "receipt.json"
    receipt_io.atomic_write_json(target, {"run_id": "original"}, exclusive=True)
    previous = target.read_bytes()
    with pytest.raises(FileExistsError):
        receipt_io.atomic_write_json(target, {"run_id": "replacement"}, exclusive=True)
    assert target.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [target]


class ConversationClient:
    def __init__(self):
        self.posts = []

    def __enter__(self): return self
    def __exit__(self, *args): pass

    def get(self, path):
        return httpx.Response(200, json={"ready": True})

    def post(self, path, *, json):
        self.posts.append(json)
        return httpx.Response(200, json={"data": [], "meta": {
            "conversation_id": "conversation", "conversation_version": len(self.posts),
            "planning": {"spec": {"action": "SEARCH"}}}})


def test_conversation_persistence_retry_does_not_repeat_http(tmp_path, monkeypatch):
    client = ConversationClient()
    target = tmp_path / "after.json"
    replace = receipt_io.os.replace
    attempts = []

    def locked_once(source, destination):
        attempts.append(json.loads(Path(source).read_text(encoding="utf-8")))
        if len(attempts) == 1:
            assert json.loads(target.read_text(encoding="utf-8"))["cases"] == []
            raise PermissionError("scanner holds receipt")
        replace(source, destination)

    monkeypatch.setattr(sys, "argv", ["evaluation", "--stage", "after", "--output", str(tmp_path),
                                      "--case", "context-and-topic-reset"])
    monkeypatch.setattr(conversation.httpx, "Client", lambda **kwargs: client)
    monkeypatch.setattr(receipt_io.os, "replace", locked_once)
    monkeypatch.setattr(receipt_io.time, "sleep", lambda seconds: None)
    conversation.main()
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert len(client.posts) == 3
    assert len(saved["cases"][0]["turns"]) == 3 and saved["finished_at"]
    assert [len(snapshot["cases"][0]["turns"]) for snapshot in attempts] == [1, 1, 2, 3, 3]
    assert attempts[0] == attempts[1]


@pytest.mark.parametrize("module", [historical, conversation])
def test_initial_publish_race_does_not_replace_other_run_receipt(tmp_path, monkeypatch, module):
    target = tmp_path / "after.json"
    winner = {"run_id": "other-evaluator"}
    link = receipt_io.os.link

    def other_writer_wins(source, destination):
        if Path(destination) == target:
            target.write_text(json.dumps(winner), encoding="utf-8")
        link(source, destination)

    args = ["evaluation", "--output", str(target)] if module is historical else [
        "evaluation", "--stage", "after", "--output", str(tmp_path), "--case", "business-specific"]
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(receipt_io.os, "link", other_writer_wins)
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: pytest.fail("must not call HTTP"))
    with pytest.raises(SystemExit, match="Refuse to overwrite"):
        module.main()
    assert json.loads(target.read_text(encoding="utf-8")) == winner
