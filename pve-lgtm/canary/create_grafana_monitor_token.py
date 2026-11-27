#!/usr/bin/env python3
"""Create a Grafana Viewer service-account token for the external monitor."""
import base64
import json
import os
import stat
import urllib.error
import urllib.request

ENV = "/opt/lgtm/.env"
BASE = "http://127.0.0.1:3000"
values = {}
for line in open(ENV, encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key] = value

user = values.get("GF_SECURITY_ADMIN_USER", "admin")
password = values["GF_SECURITY_ADMIN_PASSWORD"]
auth = base64.b64encode(f"{user}:{password}".encode()).decode()


def request(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        raw = response.read().decode()
        return json.loads(raw) if raw else None


accounts = request("GET", "/api/serviceaccounts/search?query=external-monitor")
account = next((a for a in accounts.get("serviceAccounts", []) if a.get("name") == "external-monitor"), None)
if account is None:
    account = request(
        "POST",
        "/api/serviceaccounts",
        {"name": "external-monitor", "role": "Viewer", "isDisabled": False},
    )
account_id = account["id"]

# Remove old tokens so a reinstall does not accumulate credentials.
tokens = request("GET", f"/api/serviceaccounts/{account_id}/tokens")
for token in tokens or []:
    request("DELETE", f"/api/serviceaccounts/{account_id}/tokens/{token['id']}")

created = request(
    "POST",
    f"/api/serviceaccounts/{account_id}/tokens",
    {"name": "external-monitor"},
)
secret = created["key"]

lines = open(ENV, encoding="utf-8").read().splitlines()
out = []
seen = False
for line in lines:
    if line.startswith("GRAFANA_MONITOR_TOKEN="):
        out.append(f"GRAFANA_MONITOR_TOKEN={secret}")
        seen = True
    else:
        out.append(line)
if not seen:
    out.append(f"GRAFANA_MONITOR_TOKEN={secret}")
open(ENV, "w", encoding="utf-8").write("\n".join(out) + "\n")
os.chmod(ENV, stat.S_IRUSR | stat.S_IWUSR)
print(f"service account id={account_id}, token stored")
