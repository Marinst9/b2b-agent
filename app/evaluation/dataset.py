"""Loads the synthetic company-case fixtures used by the evaluation harness
(app/evaluation/harness.py) and the demo mode (app/demo/). These fixtures are
plain JSON files under evaluation/fixtures/ -- never rows in the `leads`
table -- so they can never be confused with real leads, and are entirely
separate from the human-labeled ML data in QualificationLabel (see
database.py). Every case's `synthetic: true` marker is enforced here, not
just documented, so a malformed fixture fails loudly instead of silently
being treated as real data somewhere downstream.
"""
import json
import os
from dataclasses import dataclass, field
from typing import Optional

_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@dataclass
class EvalFact:
    category: str
    text: str
    key: Optional[str] = None
    conflicting: bool = False


@dataclass
class EvalCase:
    id: str
    company: str
    contact_name: str
    industry: str
    country: str
    website: str
    source_excerpt: str
    tags: list[str]
    notes: str
    expected_facts: list[EvalFact] = field(default_factory=list)


@dataclass
class EvalDataset:
    version: str
    campaign_offering: str
    cases: list[EvalCase]

    def by_tag(self, tag: str) -> list[EvalCase]:
        return [c for c in self.cases if tag in c.tags]


def load_dataset(name: str = "companies_v1") -> EvalDataset:
    path = os.path.join(_FIXTURES_DIR, f"{name}.json")
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    cases = []
    for raw_case in raw["cases"]:
        if not raw_case.get("synthetic"):
            raise ValueError(f"Fixture case {raw_case.get('id')!r} is missing synthetic=true -- refusing to load a fixture that isn't clearly marked synthetic.")
        facts = [
            EvalFact(category=f["category"], text=f["text"], key=f.get("key"), conflicting=f.get("conflicting", False))
            for f in raw_case.get("expected_facts", [])
        ]
        cases.append(
            EvalCase(
                id=raw_case["id"],
                company=raw_case["company"],
                contact_name=raw_case.get("contact_name", ""),
                industry=raw_case.get("industry", ""),
                country=raw_case.get("country", ""),
                website=raw_case.get("website", ""),
                source_excerpt=raw_case.get("source_excerpt", ""),
                tags=raw_case.get("tags", []),
                notes=raw_case.get("notes", ""),
                expected_facts=facts,
            )
        )

    return EvalDataset(version=raw["dataset_version"], campaign_offering=raw["campaign_offering"], cases=cases)
