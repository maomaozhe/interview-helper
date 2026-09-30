"""Versioned, closed topic taxonomy."""

from dataclasses import dataclass
from pathlib import Path

import yaml
from interview_intelligence.resources import resource_path


DEFAULT_PATH = resource_path("config/taxonomy/v1.yaml")


@dataclass(frozen=True)
class Taxonomy:
    version: str
    topics: dict[str, dict[str, str]]

    @property
    def l1_names(self) -> tuple[str, ...]:
        return tuple(self.topics)

    @property
    def leaves(self) -> tuple[str, ...]:
        return tuple(topic_id for children in self.topics.values() for topic_id in children.values())

    def resolve(self, l1: str, l2: str) -> str:
        try:
            return self.topics[l1][l2]
        except KeyError as error:
            raise ValueError(f"unknown taxonomy path: {l1}/{l2}") from error

    def labels(self, topic_id: str) -> tuple[str, str]:
        for l1, children in self.topics.items():
            for l2, value in children.items():
                if value == topic_id:
                    return l1, l2
        raise ValueError(f"unknown topic ID: {topic_id}")


def load_taxonomy(path: Path = DEFAULT_PATH) -> Taxonomy:
    with path.open(encoding="utf-8") as source:
        data = yaml.safe_load(source)
    taxonomy = Taxonomy(version=data["version"], topics=data["topics"])
    if not taxonomy.version or len(taxonomy.leaves) != len(set(taxonomy.leaves)):
        raise ValueError("taxonomy IDs must be unique and versioned")
    return taxonomy
