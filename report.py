"""Generate an offline report from supervisor metadata, never raw sessions."""

import json
from pathlib import Path
from typing import Any


def _policy_metadata(policy: dict[str, Any]) -> dict[str, Any]:
    return {key: policy[key] for key in ("root", "read", "write", "deny_read", "deny_write", "tools", "external_inputs")
            if key in policy}


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
    for worker in data.get("workers", []):
        grant = worker.get("grant", {})
        details = {key: grant[key] for key in ("task_id", "worker_id", "read", "write", "tools", "parent_id")
                   if key in grant}
        details["state"] = worker["state"]
        details["revision"] = worker.get("revision")
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
    workers = {(str(item["task"]), str(item["id"])): item for item in data.get("workers", [])}
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
    missing.append("Decision records do not establish executed effects. No effect evidence was supplied.")
    embedded = json.dumps({"nodes": nodes, "edges": valid_edges, "missing": missing}, ensure_ascii=True)
    embedded = embedded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = (Path(__file__).parent / "report" / "template.html").read_text(encoding="utf-8")
    output.write_text(template.replace("__REPORT_DATA__", embedded), encoding="utf-8")
