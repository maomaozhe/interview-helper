"""Shared ranking policy for SQL pagination and legacy statistics.

Normalization has scalar and SQL adapters; weights, bands, review multipliers
and version identity are shared so both public query paths keep one policy.
"""
import math

from sqlalchemy import case, func


SCORING_VERSIONS = {"importance": "importance_v1", "gap": "gap_v1"}
IMPORTANCE_WEIGHTS = {"frequency": 0.5, "company_coverage": 0.3, "recent_frequency": 0.2}
REVIEW_WEIGHTS = {"WEAK": 1.0, "UNSEEN": 0.8, "REVIEWED": 0.4, "MASTERED": 0.0}


def score_components(occurrences, companies, recent, *, max_occurrences, max_recent, known_companies):
    return {
        "frequency": math.log1p(occurrences) / math.log1p(max_occurrences) if max_occurrences else 0.0,
        "company_coverage": companies / known_companies if known_companies else 0.0,
        "recent_frequency": (recent or 0) / max_recent if max_recent else 0.0,
    }


def sql_score_components(groups, *, known_companies):
    return {
        "frequency": func.coalesce(
            func.ln(groups.c.occurrence_count + 1.0)
            / func.nullif(func.ln(func.max(groups.c.occurrence_count).over() + 1.0), 0), 0.0),
        "company_coverage": groups.c.company_count * 1.0 / max(1, known_companies),
        "recent_frequency": func.coalesce(
            groups.c.recent_count * 1.0 / func.nullif(func.max(groups.c.recent_count).over(), 0), 0.0),
    }


def importance_score(components):
    # Explicit addition preserves the original arithmetic order for SQL cursors.
    return (IMPORTANCE_WEIGHTS["frequency"] * components["frequency"]
            + IMPORTANCE_WEIGHTS["company_coverage"] * components["company_coverage"]
            + IMPORTANCE_WEIGHTS["recent_frequency"] * components["recent_frequency"])


def importance_band(score):
    return "CORE" if score >= 0.7 else "COMMON" if score >= 0.3 else "LONG_TAIL"


def gap_score(importance, status):
    return importance * REVIEW_WEIGHTS[status]


def sql_gap_score(importance, status):
    return importance * case(*((status == value, weight) for value, weight in REVIEW_WEIGHTS.items()), else_=0.0)
