"""Validate deployment safeguards before pushing (Python 3.11+)."""
from pathlib import Path
import tomllib
import json

root = Path(__file__).resolve().parents[1]
resources = tomllib.loads((root / "komodo/resources.toml").read_text())
bootstrap = tomllib.loads((root / "bootstrap/sync.toml").read_text())
assert not resources.get("resource_sync"), "A running sync cannot update itself"
for stack in resources["stack"]:
    assert stack["deploy"] is False, "Deploy through the ordered procedure"
    config = stack["config"]
    assert config["destroy_before_deploy"] is False, stack["name"]
    assert config["run_build"] is False, stack["name"]
    assert config["files_on_host"] is False, stack["name"]
    assert config["clone_path"].startswith("/etc/komodo/gitops/"), stack["name"]
    assert all(not file["track"] for file in config["additional_env_files"])
for sync in bootstrap["resource_sync"]:
    config = sync["config"]
    assert all(config[key] is False for key in (
        "managed", "delete", "include_variables", "include_user_groups"
    ))
print("TOML valid; deployment and secret-storage safeguards passed")

procedure = next(p for p in resources["procedure"] if p["name"] == "usbharu-lab-gitops")
deploy_order = [e["execution"]["params"]["stack"]
                for stage in procedure["config"]["stage"] for e in stage["executions"]
                if e["execution"]["type"] == "DeployStackIfChanged"]
assert deploy_order == ["ocms-observability", "ocms", "ocms-management"]
for filename in ["usbharulab-infra", "usbharulab-ocms"]:
    dashboard = json.loads((root / "stacks/observability/dashboards" / (filename + ".json")).read_text())
    assert dashboard["uid"] == filename
    ids = [p["id"] for p in dashboard["panels"]]
    assert len(ids) == len(set(ids)), "Duplicate dashboard panel IDs"
    for panel in dashboard["panels"]:
        pos = panel["gridPos"]
        assert 0 <= pos["x"] and pos["x"] + pos["w"] <= 24
print("Ordered deployment and dashboard structure passed")
