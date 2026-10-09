"""Maintain this application's HTTPS route through an existing Caddy admin API.

Adds one separately identified hostname route. Other sites are preserved. The
user service reapplies the route after a shared Caddy restart/config reload.
"""
from __future__ import annotations

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


ROUTE_ID = "interview-intelligence-site"


def request(base, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    with urlopen(Request(base.rstrip("/") + path, method=method, data=data,
                         headers={"Content-Type": "application/json"}), timeout=10) as response:
        text = response.read()
        return json.loads(text) if text else None


def reconcile(base, hostname, upstream):
    route = {"@id": ROUTE_ID, "match": [{"host": [hostname]}], "terminal": True,
             "handle": [{"handler": "reverse_proxy", "flush_interval": -1,
                         "upstreams": [{"dial": upstream}]}]}
    try:
        current = request(base, "GET", f"/id/{ROUTE_ID}")
    except HTTPError as error:
        if error.code != 404:
            raise
    else:
        if current != route:
            raise ValueError("Existing application route differs; refusing to overwrite")
        return
    servers = request(base, "GET", "/config/apps/http/servers")
    for server in servers.values():
        for existing in server.get("routes", []):
            for matcher in existing.get("match", []):
                if hostname in matcher.get("host", []):
                    raise ValueError("Hostname already belongs to another route")
    name = next(name for name, server in servers.items()
                if any(address in {":443", "0.0.0.0:443", "[::]:443"} for address in server.get("listen", [])))
    request(base, "POST", f"/config/apps/http/servers/{quote(name, safe='')}/routes", route)
    print(f"HTTPS route registered for {hostname}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-url", required=True)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--upstream", default="127.0.0.1:8092")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        try:
            reconcile(args.admin_url, args.hostname, args.upstream)
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            print(f"Caddy route check deferred: {type(error).__name__}", flush=True)
            if args.once:
                raise
        if args.once:
            break
        time.sleep(30)
