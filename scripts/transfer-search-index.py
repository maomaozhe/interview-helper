"""Export/import a single Elasticsearch alias, including its stored vectors.

Uses only the standard library. Export is read-only; import refuses an existing
alias and publishes the new alias only after every bulk operation is verified.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def request(base, method, path, body=None, *, ndjson=False):
    data = body.encode() if ndjson else None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/x-ndjson" if ndjson else "application/json"}
    with urlopen(Request(base.rstrip("/") + path, data=data, headers=headers, method=method), timeout=120) as response:
        return json.load(response)


def export_index(base, path, alias):
    if path.exists():
        raise FileExistsError(path)
    indices = request(base, "GET", f"/{alias}")
    if len(indices) != 1:
        raise ValueError("Expected exactly one physical index")
    name, definition = next(iter(indices.items()))
    count = request(base, "GET", f"/{name}/_count")["count"]
    metadata = {"alias": alias, "physical_index": name, "count": count,
                "mappings": definition["mappings"],
                "settings": {"number_of_shards": 1, "number_of_replicas": 0}}
    result = request(base, "POST", f"/{name}/_search?scroll=2m",
                     {"size": 250, "sort": ["_doc"], "query": {"match_all": {}}})
    scroll_id = result.get("_scroll_id")
    written = 0
    try:
        with gzip.open(path, "wt", encoding="utf-8") as output:
            output.write(json.dumps(metadata) + "\n")
            while result["hits"]["hits"]:
                for item in result["hits"]["hits"]:
                    output.write(json.dumps({"id": item["_id"], "source": item["_source"]}, ensure_ascii=False) + "\n")
                    written += 1
                result = request(base, "POST", "/_search/scroll", {"scroll": "2m", "scroll_id": scroll_id})
                scroll_id = result.get("_scroll_id", scroll_id)
        if written != count or list(request(base, "GET", f"/_alias/{alias}")) != [name]:
            raise ValueError("Index changed or export count mismatch")
    finally:
        if scroll_id:
            request(base, "DELETE", "/_search/scroll", {"scroll_id": [scroll_id]})
    print(json.dumps({"exported": written, "alias": alias, "physical_index": name}))


def import_index(base, path, alias):
    try:
        request(base, "GET", f"/_alias/{alias}")
    except HTTPError as error:
        if error.code != 404:
            raise
    else:
        raise ValueError("Refusing to replace an existing alias")
    with gzip.open(path, "rt", encoding="utf-8") as source:
        metadata = json.loads(next(source))
        if metadata["alias"] != alias:
            raise ValueError("Alias mismatch")
        name = f"{alias}_migration_{uuid4().hex[:12]}"
        request(base, "PUT", f"/{name}", {"settings": metadata["settings"], "mappings": metadata["mappings"]})
        batch = []
        imported = 0

        def flush():
            if not batch:
                return
            response = request(base, "POST", "/_bulk", "\n".join(batch) + "\n", ndjson=True)
            if response.get("errors"):
                errors = [item for item in response["items"] if item["index"]["status"] >= 300]
                raise ValueError(f"Bulk import failed: {errors[:3]}")
            batch.clear()

        for line in source:
            item = json.loads(line)
            batch.extend((json.dumps({"index": {"_index": name, "_id": item["id"]}}),
                          json.dumps(item["source"], ensure_ascii=False)))
            imported += 1
            if len(batch) >= 200:
                flush()
        flush()
    request(base, "POST", f"/{name}/_refresh")
    count = request(base, "GET", f"/{name}/_count")["count"]
    if count != metadata["count"] or count != imported:
        raise ValueError("Imported document count mismatch")
    request(base, "POST", "/_aliases", {"actions": [{"add": {"index": name, "alias": alias}}]})
    print(json.dumps({"imported": count, "alias": alias, "physical_index": name}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["export", "import"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--url", required=True)
    parser.add_argument("--alias", default="interview_questions")
    args = parser.parse_args()
    (export_index if args.mode == "export" else import_index)(args.url, args.path, args.alias)
