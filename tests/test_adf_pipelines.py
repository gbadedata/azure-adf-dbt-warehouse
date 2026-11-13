"""Static checks on the Data Factory pipeline definitions.

Data Factory only validates a pipeline when it is deployed, so these catch the
mistakes that would otherwise surface in Azure: broken JSON, an activity that
depends on a name that does not exist, or a reference to a dataset or pipeline
that Terraform never creates.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "adf" / "pipelines"
TERRAFORM = ROOT / "infra" / "terraform"
VALUES = {
    "env": "prod",
    "subscription_id": "00000000-0000-0000-0000-000000000000",
    "resource_group": "rg-wh-abcde",
    "ingest_job": "caj-ingest-prod-abcde",
    "dbt_job": "caj-dbt-prod-abcde",
}


def render(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    assert "%{" not in text, "template directives are not used here"
    used = set(re.findall(r"\$\{(\w+)\}", text))
    assert used <= set(VALUES), f"unknown template variables {used - set(VALUES)}"
    for key, value in VALUES.items():
        text = text.replace("${" + key + "}", value)
    return json.loads(text)


def terraform_names(prefix: str) -> set[str]:
    names = set()
    for tf in TERRAFORM.glob("*.tf"):
        for match in re.finditer(r'name\s*=\s*"(' + prefix + r'[a-z_]*)\$\{each\.key\}"', tf.read_text()):
            names.add(match.group(1) + VALUES["env"])
    return names


def walk(activities: list[dict]):
    """Yield each scope (a list of sibling activities), including nested ones."""
    yield activities
    for activity in activities:
        props = activity.get("typeProperties", {})
        for key in ("activities", "ifTrueActivities", "ifFalseActivities"):
            if key in props:
                yield from walk(props[key])


@pytest.mark.parametrize("path", sorted(TEMPLATES.glob("*.json.tftpl")), ids=lambda p: p.name)
def test_pipeline_is_well_formed(path):
    activities = render(path)
    datasets = terraform_names("ds_")
    pipelines = terraform_names("pl_")
    assert datasets and pipelines

    for scope in walk(activities):
        names = [a["name"] for a in scope]
        assert len(names) == len(set(names)), f"duplicate activity names in {path.name}"
        for activity in scope:
            assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,54}", activity["name"])
            for dependency in activity.get("dependsOn", []):
                assert dependency["activity"] in names, f"{activity['name']} depends on a missing activity"
                assert set(dependency["dependencyConditions"]) <= {"Succeeded", "Failed", "Completed", "Skipped"}

    text = json.dumps(activities)
    for reference in re.findall(r'"referenceName": "([^"]+)", "type": "DatasetReference"', text):
        assert reference in datasets, f"dataset {reference} is not defined in Terraform"
    for reference in re.findall(r'"referenceName": "([^"]+)", "type": "PipelineReference"', text):
        assert reference in pipelines, f"pipeline {reference} is not defined in Terraform"
    for name in re.findall(r"activity\('(\w+)'\)", text):
        assert any(name in [a["name"] for a in scope] for scope in walk(activities)), f"unknown activity {name}"


def test_dbt_runs_only_after_both_loads_succeed():
    daily = {a["name"]: a for a in render(TEMPLATES / "daily.json.tftpl")}
    upstream = {(d["activity"], tuple(d["dependencyConditions"])) for d in daily["BuildDbt"]["dependsOn"]}
    assert upstream == {("LoadCrmApi", ("Succeeded",)), ("LoadEventFiles", ("Succeeded",))}


def test_files_are_archived_only_after_a_successful_upsert():
    pipeline = render(TEMPLATES / "load_event_files.json.tftpl")
    for_each = next(a for a in pipeline if a["name"] == "ForEachFile")
    inner = {a["name"]: a for a in for_each["typeProperties"]["activities"]}
    assert inner["ArchiveFile"]["dependsOn"] == [
        {"activity": "UpsertRegistrations", "dependencyConditions": ["Succeeded"]}
    ]
    sink = inner["UpsertRegistrations"]["typeProperties"]["sink"]
    assert sink["writeBehavior"] == "upsert" and sink["upsertSettings"]["keys"] == ["registration_id"]


def test_upsert_maps_every_raw_column():
    raw = (ROOT / "sql" / "020_raw.sql").read_text()
    table = raw[raw.index("raw.event_registrations (") : raw.index(");", raw.index("raw.event_registrations ("))]
    columns = re.findall(r"^\s+(\w+)\s+NVARCHAR|^\s+(\w+)\s+DATETIME2", table, flags=re.M)
    columns = {a or b for a, b in columns}
    pipeline = render(TEMPLATES / "load_event_files.json.tftpl")
    copy = next(a for a in pipeline if a["name"] == "ForEachFile")["typeProperties"]["activities"][0]
    mapped = {m["sink"]["name"] for m in copy["typeProperties"]["translator"]["mappings"]}
    assert mapped == columns


def test_a_failed_job_fails_the_pipeline():
    run_job = {a["name"]: a for a in render(TEMPLATES / "run_job.json.tftpl")}
    on_failure = run_job["CheckResult"]["typeProperties"]["ifFalseActivities"]
    assert [a["type"] for a in on_failure] == ["Fail"]
    assert run_job["StartJob"]["typeProperties"]["disableAsyncPattern"] is True
