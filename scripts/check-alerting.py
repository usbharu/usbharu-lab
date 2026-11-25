"""Validate Grafana alerting provisioning files.

Run with Python 3.11+ after installing PyYAML:

    python3 -m pip install pyyaml
    python3 scripts/check-alerting.py

The script checks the file-provisioned alert rules, contact points, notification
policies, templates and mute timings before they are copied to LXC 200.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML is required: python3 -m pip install pyyaml", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parents[1]
ALERT_DIR = ROOT / "pve-lgtm" / "provisioning" / "alerting"
DASHBOARDS_FILE = ROOT / "pve-lgtm" / "provisioning" / "dashboards.yaml"
RUNBOOK_FILE = ROOT / "pve-lgtm" / "ALERTING.md"
RUNBOOK_PREFIX = "https://github.com/usbharu/usbharu-lab/blob/komodo/pve-lgtm/ALERTING.md#"
UID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
ISSUE_RE = re.compile(r"^[0-9]+$")
SECRET_PATTERN = "discord.com/api/webhooks/"

errors: list[str] = []
warnings: list[str] = []


def error(message: str) -> None:
    errors.append(message)


def warn(message: str) -> None:
    warnings.append(message)


def load(path: Path) -> dict:
    try:
        document = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        error(f"{path.name}: invalid YAML: {exc}")
        return {}
    if not isinstance(document, dict):
        error(f"{path.name}: expected a mapping at the top level")
        return {}
    if document.get("apiVersion") != 1:
        error(f"{path.name}: apiVersion must be 1")
    return document


files = sorted(ALERT_DIR.glob("*.yaml"))
if not files:
    error(f"no provisioning files found in {ALERT_DIR}")

documents = {path: load(path) for path in files}

template_names: set[str] = set()
contact_point_names: set[str] = set()
receiver_uids: set[str] = set()
group_names: set[str] = set()
rule_uids: set[str] = set()
rule_titles: set[str] = set()
policy_receivers: list[tuple[str, str]] = []
rule_count = 0
issue_counts: Counter[str] = Counter()

# --- templates -------------------------------------------------------------
for path, document in documents.items():
    for template in document.get("templates") or []:
        name = template.get("name")
        content = template.get("template")
        if not name:
            error(f"{path.name}: template without a name")
            continue
        if name in template_names:
            error(f"{path.name}: duplicate template name {name!r}")
        template_names.add(name)
        if not isinstance(content, str) or not content.strip():
            error(f"{path.name}: template {name!r} has no content")

# --- contact points --------------------------------------------------------
for path, document in documents.items():
    for contact_point in document.get("contactPoints") or []:
        name = contact_point.get("name")
        if not name:
            error(f"{path.name}: contact point without a name")
            continue
        if name in contact_point_names:
            error(f"{path.name}: duplicate contact point name {name!r}")
        contact_point_names.add(name)
        receivers = contact_point.get("receivers") or []
        if not receivers:
            error(f"{path.name}: contact point {name!r} has no receivers")
        for receiver in receivers:
            uid = receiver.get("uid")
            rtype = receiver.get("type")
            if not uid:
                error(f"{path.name}: receiver in {name!r} has no uid")
            elif not UID_RE.match(str(uid)):
                error(f"{path.name}: invalid receiver uid {uid!r}")
            elif uid in receiver_uids:
                error(f"{path.name}: duplicate receiver uid {uid!r}")
            else:
                receiver_uids.add(uid)
            if not rtype:
                error(f"{path.name}: receiver {uid!r} has no type")
            settings = receiver.get("settings") or {}
            if rtype == "discord":
                url = settings.get("url")
                if url != "$GRAFANA_DISCORD_WEBHOOK_URL":
                    error(
                        f"{path.name}: Discord url must be "
                        "$GRAFANA_DISCORD_WEBHOOK_URL, got {url!r}"
                    )

# --- notification policies -------------------------------------------------
def check_route(route: dict, path: Path, depth: int = 0) -> None:
    receiver = route.get("receiver")
    if not receiver:
        error(f"{path.name}: policy route at depth {depth} has no receiver")
    elif receiver not in contact_point_names:
        error(f"{path.name}: policy receiver {receiver!r} is not defined")
    else:
        policy_receivers.append((path.name, receiver))
    for child in route.get("routes") or []:
        check_route(child, path, depth + 1)


for path, document in documents.items():
    policies = document.get("policies") or []
    for policy in policies:
        if not policy.get("receiver"):
            error(f"{path.name}: root policy has no receiver")
        else:
            policy_receivers.append((path.name, policy["receiver"]))
        if not policy.get("group_by"):
            warn(f"{path.name}: root policy has no group_by")
        for route in policy.get("routes") or []:
            check_route(route, path)

# --- mute timings ----------------------------------------------------------
mute_names: set[str] = set()
for path, document in documents.items():
    for mute in document.get("muteTimes") or []:
        name = mute.get("name")
        if not name:
            error(f"{path.name}: mute timing without a name")
            continue
        if name in mute_names:
            error(f"{path.name}: duplicate mute timing {name!r}")
        mute_names.add(name)
        if not mute.get("time_intervals"):
            error(f"{path.name}: mute timing {name!r} has no time_intervals")

# --- alert rules -----------------------------------------------------------
for path, document in documents.items():
    for group in document.get("groups") or []:
        name = group.get("name")
        folder = group.get("folder")
        if not name:
            error(f"{path.name}: rule group without a name")
            continue
        if name in group_names:
            error(f"{path.name}: duplicate rule group name {name!r}")
        group_names.add(name)
        if folder != "Alerts":
            error(f"{path.name}: group {name!r} must use folder 'Alerts'")
        if not group.get("interval"):
            error(f"{path.name}: group {name!r} has no interval")
        rules = group.get("rules") or []
        if not rules:
            error(f"{path.name}: group {name!r} has no rules")

        for rule in rules:
            rule_count += 1
            uid = rule.get("uid")
            title = rule.get("title")
            label = f"{path.name}: rule {uid or title or '<unknown>'!r}"

            if not uid:
                error(f"{label}: no uid")
            elif not UID_RE.match(str(uid)):
                error(f"{label}: uid must match {UID_RE.pattern}")
            elif uid in rule_uids:
                error(f"{label}: duplicate uid")
            else:
                rule_uids.add(uid)

            if not title:
                error(f"{label}: no title")
            elif title in rule_titles:
                error(f"{label}: duplicate title")
            else:
                rule_titles.add(title)

            data = rule.get("data") or []
            if len(data) < 2:
                error(f"{label}: data must contain at least the query and threshold")
                continue
            by_ref = {}
            for item in data:
                ref = item.get("refId")
                if not ref:
                    error(f"{label}: data item without refId")
                    continue
                if ref in by_ref:
                    error(f"{label}: duplicate refId {ref!r}")
                by_ref[ref] = item

            condition = rule.get("condition")
            if not condition:
                error(f"{label}: no condition")
                continue
            if condition not in by_ref:
                error(f"{label}: condition {condition!r} is not in data")
                continue

            threshold = by_ref[condition]
            if threshold.get("datasourceUid") != "__expr__":
                error(f"{label}: condition must use __expr__ datasource")
            model = threshold.get("model") or {}
            if model.get("type") != "threshold":
                error(f"{label}: condition expression must be type threshold")
            source_ref = model.get("expression")
            if source_ref not in by_ref:
                error(f"{label}: threshold expression {source_ref!r} is not in data")
                continue
            source = by_ref[source_ref]
            datasource = source.get("datasourceUid")
            if datasource not in ("mimir", "loki"):
                error(f"{label}: query datasource must be mimir or loki, got {datasource!r}")
            query_model = source.get("model") or {}
            expr = query_model.get("expr")
            if not isinstance(expr, str) or not expr.strip():
                error(f"{label}: query expression is empty")
            conditions = model.get("conditions") or []
            if not conditions:
                error(f"{label}: threshold has no conditions")
            else:
                evaluator = conditions[0].get("evaluator") or {}
                if evaluator.get("type") not in {
                    "gt",
                    "lt",
                    "gte",
                    "lte",
                    "eq",
                    "neq",
                }:
                    error(f"{label}: unsupported evaluator {evaluator.get('type')!r}")
                if not evaluator.get("params"):
                    error(f"{label}: threshold evaluator has no params")

            if rule.get("noDataState") not in {"Alerting", "OK", "NoData"}:
                error(f"{label}: invalid noDataState {rule.get('noDataState')!r}")
            if rule.get("execErrState") not in {"Alerting", "OK", "Error"}:
                error(f"{label}: invalid execErrState {rule.get('execErrState')!r}")
            if not rule.get("for"):
                error(f"{label}: no for duration")

            labels = rule.get("labels") or {}
            if labels.get("severity") not in {"critical", "warning"}:
                error(f"{label}: severity must be critical or warning")
            if not labels.get("category"):
                error(f"{label}: category label is missing")
            if not ISSUE_RE.match(str(labels.get("issue", ""))):
                error(f"{label}: issue label must be a number")
            if not labels.get("issue"):
                error(f"{label}: issue label is missing")
            issue_counts[str(labels.get("issue"))] += 1

            annotations = rule.get("annotations") or {}
            if not annotations.get("summary"):
                error(f"{label}: summary annotation is missing")
            if not annotations.get("description"):
                error(f"{label}: description annotation is missing")
            runbook = annotations.get("runbook_url")
            if not isinstance(runbook, str) or not runbook.startswith(RUNBOOK_PREFIX):
                error(f"{label}: runbook_url must start with {RUNBOOK_PREFIX}")

# --- runbook anchors -------------------------------------------------------
runbook_anchors = set()
if RUNBOOK_FILE.exists():
    runbook_anchors = set(
        re.findall(r'<a id="([^"]+)"></a>', RUNBOOK_FILE.read_text())
    )
else:
    error("pve-lgtm/ALERTING.md is missing")

for path, document in documents.items():
    for group in document.get("groups") or []:
        for rule in group.get("rules") or []:
            runbook = (rule.get("annotations") or {}).get("runbook_url", "")
            if isinstance(runbook, str) and runbook.startswith(RUNBOOK_PREFIX):
                anchor = runbook[len(RUNBOOK_PREFIX) :]
                if anchor not in runbook_anchors:
                    error(
                        f"{path.name}: runbook anchor #{anchor} is missing "
                        "from pve-lgtm/ALERTING.md"
                    )

# --- secrets ---------------------------------------------------------------
for path in files:
    if SECRET_PATTERN in path.read_text():
        error(f"{path.name}: contains a literal Discord webhook URL")

# --- dashboards ------------------------------------------------------------
if not DASHBOARDS_FILE.exists():
    error("pve-lgtm/provisioning/dashboards.yaml is missing")
else:
    try:
        providers = yaml.safe_load(DASHBOARDS_FILE.read_text()).get("providers") or []
    except yaml.YAMLError as exc:
        error(f"dashboards.yaml: invalid YAML: {exc}")
        providers = []
    if not providers:
        error("dashboards.yaml: no providers")
    for provider in providers:
        if provider.get("allowUiUpdates") is not False:
            error(
                "dashboards.yaml: provider "
                f"{provider.get('name')!r} must set allowUiUpdates: false"
            )

# --- report ----------------------------------------------------------------
if warnings:
    print("Warnings:")
    for message in warnings:
        print(f"  - {message}")

if errors:
    print("Errors:", file=sys.stderr)
    for message in errors:
        print(f"  - {message}", file=sys.stderr)
    sys.exit(1)

print(
    f"Alerting valid: {rule_count} rules in {len(group_names)} groups, "
    f"{len(contact_point_names)} contact point(s), {len(policy_receivers)} policy route(s)."
)
for issue, count in sorted(issue_counts.items(), key=lambda item: int(item[0])):
    print(f"  issue #{issue}: {count} rule(s)")
