"""Validate deployment safeguards before pushing (Python 3.11+)."""
from pathlib import Path
import tomllib

root = Path(__file__).resolve().parents[1]
resources = tomllib.loads((root / "komodo/resources.toml").read_text())
bootstrap = tomllib.loads((root / "bootstrap/sync.toml").read_text())
assert not resources.get("resource_sync"), "A running sync cannot update itself"
for stack in resources["stack"]:
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
