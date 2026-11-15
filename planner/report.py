"""Generate an offline report from supervisor metadata, never raw sessions."""

import json
from pathlib import Path
from typing import Any

from .policy import covered


def _policy_metadata(policy: dict[str, Any]) -> dict[str, Any]:
    return {key: policy[key] for key in ("root", "read", "write", "deny_read", "deny_write", "tools", "external_inputs")
            if key in policy}


def _grant_metadata(grant: dict[str, Any]) -> dict[str, Any]:
    return {key: grant[key] for key in ("task_id", "worker_id", "read", "write", "tools", "parent_id")
            if key in grant}


def _subset(grant: dict[str, Any], ceiling: dict[str, Any]) -> bool:
    return (covered(tuple(grant.get("read", [])), tuple(ceiling.get("read", [])))
            and covered(tuple(grant.get("write", [])), tuple(ceiling.get("write", [])))
            and set(grant.get("tools", [])) <= set(ceiling.get("tools", [])))


def _attempt_metadata(attempt: dict[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {key: attempt[key] for key in ("id", "task", "worker", "attempt", "revision", "expires",
               "state", "reason", "created", "updated", "cleanup_verified") if key in attempt}
    details["checks"] = [{key: check[key] for key in ("backend", "available", "verified") if key in check}
                         for check in attempt.get("checks", [])]
    if "grant" in attempt:
        details["grant"] = _grant_metadata(attempt["grant"])
    return details


def _attempt_status(attempt: dict[str, Any]) -> str:
    cleanup = "cleanup evidence recorded" if attempt.get("cleanup_verified") else "cleanup unverified"
    return f"{attempt['state']}; {cleanup}"


_APPLICATION_STATUS = {
    "applied": "applied to source",
    "retained": "executed in copy; retained for review",
    "conflicted": "conflicted; source preserved",
    "blocked": "blocked before any source write",
    "interrupted": "interrupted; needs reconciliation",
    "applying": "unverified; application owner did not finish",
    "not_applied": "not applied",
}


def generate(data: dict[str, Any], output: Path) -> None:
    provenance = data.get("provenance", data)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    missing: list[str] = []
    notes = {str(note["id"]): note for note in provenance.get("notes", [])}

    for record in provenance.get("nodes", []):
        details = {key: record[key] for key in ("id", "task_id", "worker_id", "kind",
                   "reference", "origins", "sensitive", "created_at") if key in record}
        status = "sensitive metadata" if record.get("sensitive") else "observed"
        note = notes.get(str(record["id"]))
        if note is not None:
            details["deleted_at"] = note.get("deleted_at")
            details["project_id"] = note.get("project_id")
            if note.get("deleted_at") is not None:
                status = "deleted note"
        nodes.append({"id": str(record["id"]), "task": str(record["task_id"]),
                      "worker": str(record["worker_id"]), "kind": str(record["kind"]),
                      "label": str(record.get("reference") or record["kind"]),
                      "status": status,
                      "time": record.get("created_at"), "details": details})
    for task in data.get("tasks", []):
        details = {key: task[key] for key in ("id", "state", "revision", "expires") if key in task}
        details["policy"] = _policy_metadata(task.get("policy", {}))
        nodes.append({"id": f"task:{task['id']}", "task": str(task["id"]), "worker": "",
                      "kind": "task", "label": f"Current task · r{task['revision']}",
                      "status": str(task["state"]), "time": None, "details": details})
    revisions = {(str(item["task"]), item["revision"]): item for item in data.get("policy_revisions", [])}
    for revision in revisions.values():
        details = {key: revision[key] for key in ("task", "revision", "created", "approved") if key in revision}
        details["policy"] = _policy_metadata(revision.get("policy", {}))
        nodes.append({"id": f"policy:{revision['task']}:{revision['revision']}",
                      "task": str(revision["task"]), "worker": "", "kind": "policy",
                      "label": f"Policy r{revision['revision']}",
                      "status": "approved" if revision.get("approved") is not None else "proposed",
                      "time": revision.get("created"), "details": details})
    workers = {(str(item["task"]), str(item["id"])): item for item in data.get("workers", [])}
    templates = {(str(item["task"]), item["revision"], str(item["name"])): item
                 for item in data.get("delegation_templates", [])}
    valid_templates = set()
    for key, preset in templates.items():
        node_id = f"template:{preset['task']}:{preset['revision']}:{preset['name']}"
        details = {name: preset[name] for name in ("task", "name", "parent", "revision", "state", "created")
                   if name in preset}
        details["grant"] = _grant_metadata(preset.get("grant", {}))
        nodes.append({"id": node_id, "task": str(preset["task"]), "worker": str(preset["parent"]),
                      "kind": "delegation_template", "label": f"Delegation preset {preset['name']}",
                      "status": f"user-approved preset; {preset['state']}",
                      "time": preset.get("created"), "details": details})
        revision = revisions.get((str(preset["task"]), preset["revision"]))
        parent = workers.get((str(preset["task"]), str(preset["parent"])))
        exact = (revision is not None and revision.get("approved") is not None
                 and parent is not None and parent.get("revision") == preset["revision"]
                 and preset.get("grant", {}).get("parent_id") == preset["parent"]
                 and _subset(preset.get("grant", {}), parent.get("grant", {})))
        if exact:
            valid_templates.add(key)
            edges.append({"source": f"policy:{preset['task']}:{preset['revision']}",
                          "target": node_id, "kind": "authorization"})
            edges.append({"source": f"worker:{preset['task']}:{preset['parent']}",
                          "target": node_id, "kind": "authorization"})
        else:
            missing.append(f"Delegation template policy or parent mismatch for {node_id}")
    for worker in data.get("workers", []):
        grant = worker.get("grant", {})
        details = _grant_metadata(grant)
        details["state"] = worker["state"]
        details["revision"] = worker.get("revision")
        details["template"] = worker.get("template")
        node_id = f"worker:{worker['task']}:{worker['id']}"
        nodes.append({"id": node_id, "task": str(worker["task"]), "worker": str(worker["id"]),
                      "kind": "grant", "label": f"Worker {worker['id']}",
                      "status": str(worker["state"]), "time": None, "details": details})
        revision = revisions.get((str(worker["task"]), worker.get("revision")))
        if revision is not None and revision.get("approved") is not None:
            edges.append({"source": f"policy:{worker['task']}:{worker['revision']}",
                          "target": node_id, "kind": "authorization"})
        else:
            missing.append(f"No approved policy revision established for installed grant {node_id}")
        if worker.get("template"):
            key = (str(worker["task"]), worker.get("revision"), str(worker["template"]))
            preset = templates.get(key)
            exact = (key in valid_templates and preset is not None
                     and grant.get("task_id") == worker["task"] and grant.get("worker_id") == worker["id"]
                     and grant.get("parent_id") == preset["parent"]
                     and _subset(grant, preset.get("grant", {})))
            if exact:
                edges.append({"source": f"template:{worker['task']}:{worker['revision']}:{worker['template']}",
                              "target": node_id, "kind": "authorization"})
            else:
                missing.append(f"Delegation template binding missing or mismatched for installed grant {node_id}")
    for attempt in data.get("attempts", []):
        node_id = f"attempt:{attempt['id']}"
        nodes.append({"id": node_id, "task": str(attempt["task"]), "worker": str(attempt["worker"]),
                      "kind": "attempt", "label": f"Attempt snapshot {attempt['id']}",
                      "status": _attempt_status(attempt), "time": attempt.get("updated"),
                      "details": _attempt_metadata(attempt)})
        worker = workers.get((str(attempt["task"]), str(attempt["worker"])))
        snapshot = attempt.get("grant", {})
        revision = revisions.get((str(attempt["task"]), attempt.get("revision")))
        exact = (worker is not None and worker.get("revision") == attempt.get("revision")
                 and revision is not None and revision.get("approved") is not None
                 and snapshot.get("task_id") == attempt["task"]
                 and snapshot.get("worker_id") == attempt["worker"]
                 and _grant_metadata(snapshot) == _grant_metadata(worker.get("grant", {})))
        if exact:
            edges.append({"source": f"worker:{attempt['task']}:{attempt['worker']}",
                          "target": node_id, "kind": "authorization"})
        else:
            missing.append(f"Execution attempt grant snapshot missing or mismatched for {node_id}")
        previous = node_id
        for event in attempt.get("events", []):
            event_id = f"attempt-event:{attempt['id']}:{event['id']}"
            nodes.append({"id": event_id, "task": str(attempt["task"]), "worker": str(attempt["worker"]),
                          "kind": "attempt_event", "label": f"Attempt transition · {event['state']}",
                          "status": _attempt_status(event), "time": event.get("created"),
                          "details": _attempt_metadata(event)})
            if event.get("attempt") == attempt["id"]:
                edges.append({"source": previous, "target": event_id, "kind": "lifecycle"})
                previous = event_id
            else:
                missing.append(f"Lifecycle event attempt binding mismatch for {event_id}")
    attempt_ids = {str(item["id"]) for item in data.get("attempts", [])}
    for application in data.get("applications", []):
        node_id = f"application:{application['attempt']}"
        details = {key: application[key] for key in ("attempt", "task", "worker", "mode", "state", "reason",
                   "changes", "updated") if key in application}
        nodes.append({"id": node_id, "task": str(application["task"]), "worker": str(application["worker"]),
                      "kind": "application", "label": f"Source application · {application['mode']}",
                      "status": _APPLICATION_STATUS.get(str(application["state"]), "unverified"),
                      "time": application.get("updated"), "details": details})
        if str(application["attempt"]) in attempt_ids:
            edges.append({"source": f"attempt:{application['attempt']}", "target": node_id, "kind": "lifecycle"})
        else:
            missing.append(f"Application {node_id} has no recorded execution attempt")
    for approval in data.get("approvals", []):
        details = {key: approval[key] for key in ("id", "task", "worker", "operation", "resource", "tool",
                   "lifetime", "expires", "state", "sensitive", "revision", "sources") if key in approval}
        nodes.append({"id": f"approval:{approval['id']}", "task": str(approval["task"]),
                      "worker": str(approval["worker"]), "kind": "approval",
                      "label": f"{approval['operation']} {approval['resource']}",
                      "status": str(approval["state"]), "time": None, "details": details})
        sources = approval.get("sources")
        if isinstance(sources, str):
            try:
                sources = json.loads(sources)
            except json.JSONDecodeError:
                pass
        if sources:
            missing.append(f"Approval {approval['id']} source provenance is unverified; claimed references remain details only.")
    approvals = {str(item["id"]): item for item in data.get("approvals", [])}
    for decision in data.get("decisions", []):
        details = {key: decision[key] for key in ("id", "task", "worker", "operation", "resource", "tool",
                   "allowed", "reason", "approval", "created", "grant_revision") if key in decision}
        node_id = f"decision:{decision['task']}:{decision['id']}"
        nodes.append({"id": node_id, "task": str(decision["task"]), "worker": str(decision["worker"]),
                      "kind": "decision", "label": f"{decision['operation']} {decision['resource']}",
                      "status": "allowed; effect unverified" if decision["allowed"] else "denied",
                      "time": decision.get("created"), "details": details})
        if decision.get("approval"):
            approval = approvals.get(str(decision["approval"]))
            exact = (approval is not None and all(approval.get(key) == decision.get(key)
                     for key in ("task", "worker", "operation", "resource", "tool")))
            if exact:
                edges.append({"source": f"approval:{decision['approval']}", "target": node_id,
                              "kind": "authorization"})
            else:
                missing.append(f"Approval mismatch or missing record for decision {node_id}")
        elif decision["allowed"]:
            worker = workers.get((str(decision["task"]), str(decision["worker"])))
            grant_revision = decision.get("grant_revision")
            revision = revisions.get((str(decision["task"]), grant_revision))
            exact = (worker is not None and grant_revision is not None
                     and worker.get("revision") == grant_revision
                     and revision is not None and revision.get("approved") is not None)
            if exact:
                edges.append({"source": f"worker:{decision['task']}:{decision['worker']}",
                              "target": node_id, "kind": "authorization"})
            else:
                missing.append(f"Installed grant revision missing or mismatched for decision {node_id}")
    edges.extend({"source": str(edge["source"]), "target": str(edge["target"]), "kind": "information"}
                 for edge in provenance.get("edges", []))
    identifiers = {node["id"] for node in nodes}
    if len(identifiers) != len(nodes):
        raise ValueError("Report contains duplicate record identifiers")
    policy_tasks = {str(task["id"]) for task in data.get("tasks", [])}
    for task_id in sorted({node["task"] for node in nodes} - policy_tasks):
        missing.append(f"No approved-policy snapshot supplied for task {task_id}")
    for node in nodes:
        for origin in node["details"].get("origins", []):
            if origin not in identifiers:
                missing.append(f"Missing origin record {origin} referenced by {node['id']}")
    valid_edges = []
    for edge in edges:
        if edge["source"] in identifiers and edge["target"] in identifiers:
            valid_edges.append(edge)
        else:
            missing.append(f"Missing endpoint for {edge['kind']} link: {edge['source']} → {edge['target']}")
    if not data.get("tasks"):
        missing.append("Approved task policies and installed grants were not supplied.")
    if not revisions:
        missing.append("Policy revision history and its lifecycle timestamps were not supplied.")
    missing.append("Approval records are current snapshots; approval lifecycle timestamps are unavailable.")
    if data.get("applications"):
        missing.append("Decision records do not establish executed effects; only application records "
                       "describe changes to the original project.")
    else:
        missing.append("Decision records do not establish executed effects. No effect evidence was supplied.")
    embedded = json.dumps({"nodes": nodes, "edges": valid_edges, "missing": missing}, ensure_ascii=True)
    embedded = embedded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = (Path(__file__).parent / "report" / "template.html").read_text(encoding="utf-8")
    output.write_text(template.replace("__REPORT_DATA__", embedded), encoding="utf-8")
