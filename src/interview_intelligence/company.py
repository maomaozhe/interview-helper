"""Exact company aliases shared by ingestion, query contracts and model context."""

COMPANY_ALIASES = {
    "字节跳动": "字节", "抖音": "字节", "腾讯音乐": "腾讯音乐", "阿里云": "阿里云",
    "pdd": "拼多多", "boss": "BOSS直聘", "boss直聘": "BOSS直聘",
}


def normalize_company(raw: str | None) -> str | None:
    if not raw:
        return None
    return COMPANY_ALIASES.get(raw.casefold(), raw)
