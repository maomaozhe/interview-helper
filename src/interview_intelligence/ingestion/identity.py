"""Stable source identity based on explicit source references."""

import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


URL_PATTERN = re.compile(r"https?://[^\s<>]+")
XHS_POST_PATTERN = re.compile(r"/(?:explore|discovery/item)/([A-Za-z0-9]+)")


def source_identity(text: str, relative_path: str) -> tuple[str, str | None, str]:
    """Return identity, source URL, and source type without inferring a URL."""
    match = URL_PATTERN.search(text)
    if not match:
        return f"path:{relative_path.casefold()}", None, "file"
    raw_url = match.group(0).rstrip(".,，。)）")
    parsed = urlsplit(raw_url)
    hostname = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if "xiaohongshu.com" in hostname:
        post = XHS_POST_PATTERN.search(path)
        if post:
            return f"xiaohongshu:{post.group(1)}", raw_url, "xiaohongshu"
    if "nowcoder.com" in hostname:
        post = re.search(r"/(?:discuss|feed)/([A-Za-z0-9]+)", path)
        if post:
            return f"nowcoder:{post.group(1)}", raw_url, "nowcoder"
    tracking_names = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"}
    query = urlencode(sorted((key, value) for key, value in parse_qsl(parsed.query) if key not in tracking_names))
    normalized = urlunsplit((parsed.scheme.lower(), hostname, path, query, ""))
    return f"url:{normalized}", raw_url, hostname or "web"
