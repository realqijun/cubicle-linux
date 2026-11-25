"""Task proposals from user instructions and trusted templates, before approval.

The planning model receives no project files and cannot approve its own proposal.
"""

from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request

from .connector import ConnectorConfig
from .context import ModelContext
from .policy import Policy, validate_patterns
from .provenance import Provenance
from .records import Grant
from .supervisor import Supervisor

TEMPLATES = ("source", "inspection", "tests", "documentation")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        raise ValueError("Provider redirects are not permitted")


def plan(supervisor: Supervisor, project: Path, prompt: str, config: ConnectorConfig,
         *, credential: str | None = None, worker_id: str = "worker",
         max_response_bytes: int = 1024 * 1024) -> dict[str, Any]:
    """Call the explicit provider and persist a proposal, never an approval.

    Raw prompts, model output and credentials stay in memory. Only the validated
    policy and provenance metadata enter the supervisor stores. Failed planning
    leaves a cancelled task for inspection rather than an active assignment.
    """
    if not prompt.strip() or not re.fullmatch(r"[A-Za-z0-9_-]+", worker_id):
        raise ValueError("A task prompt and simple worker identifier are required")
    provider = config.provider
    if provider is None or bool(provider.api_key_env) != bool(credential):
        raise ValueError("An explicit provider and its configured credential are required")
    if max_response_bytes < 1 or not math.isfinite(config.timeout_seconds):
        raise ValueError("Planning limits must be positive and finite")
    policies = {name: Policy.load(project, Path(__file__).parent / "templates" / f"{name}.toml")
                for name in TEMPLATES}
    fields = ("read", "write", "deny_read", "deny_write", "tools")
    catalog = {name: {field: policy.as_dict()[field] for field in fields}
               for name, policy in policies.items()}
    instructions = (
        "Select one trusted template and narrower worker scopes for the user's task. "
        "Return exactly one JSON object with keys template, read, write, tools. "
        "Scopes and tools must be subsets of the selected template. "
        "No project information is available at this stage. Do not claim user approval. "
        "Trusted template catalog:\n" + json.dumps(catalog, sort_keys=True)
    )
    task = supervisor.propose(Policy(project))
    store = None
    context = None
    try:
        store = Provenance(supervisor.path.with_suffix(".memory.sqlite"))
        context = ModelContext(store, task, "planner")
        store.register_task(task, str(supervisor.policy(task).root))
        handles = (context.observe(instructions, "trusted_template_catalog", "bundled-templates"),
                   context.observe(prompt, "user_instruction", "operator-task-prompt"))

        def request(inputs: tuple[str, ...], json_mode: bool) -> bytes:
            body: dict[str, Any] = {"model": config.model.split("/", 1)[1], "stream": False,
                                    "max_tokens": provider.output_tokens,
                                    "messages": [{"role": "system", "content": inputs[0]},
                                                 {"role": "user", "content": inputs[1]}]}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            outgoing = Request(provider.base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"}, method="POST")
            if credential:
                outgoing.add_header("Authorization", "Bearer " + credential)
            # Do not inherit proxy credentials/settings or follow cross-origin redirects.
            with build_opener(ProxyHandler({}), _NoRedirect()).open(outgoing, timeout=config.timeout_seconds) as response:
                return bytes(response.read(max_response_bytes + 1))

        def transport(inputs: tuple[str, ...]) -> str:
            try:
                raw = request(inputs, json_mode=True)
            except HTTPError as error:
                error.close()
                if error.code != 400:
                    raise
                # Some servers reject JSON mode; the proposal is validated strictly either way.
                raw = request(inputs, json_mode=False)
            if len(raw) > max_response_bytes:
                raise ValueError("Planning response exceeds configured limit")
            result = json.loads(raw)["choices"][0]["message"]["content"]
            if not isinstance(result, str):
                raise ValueError("Provider did not return a text proposal")
            return result

        output = context.call(handles, transport, kind="planner_model_call")
        proposal = json.loads(context.text(output))
        if not isinstance(proposal, dict) or set(proposal) != {"template", "read", "write", "tools"}:
            raise ValueError("Planner must return only a template, read/write scopes and tools")
        name = proposal["template"]
        if not isinstance(name, str) or name not in policies:
            raise ValueError("Unknown trusted task template")
        tools = proposal["tools"]
        if not isinstance(tools, list) or any(not isinstance(tool, str) or not tool for tool in tools):
            raise ValueError("Planner tools must be a list of names")
        grant = Grant(task, worker_id, validate_patterns(proposal["read"]),
                      validate_patterns(proposal["write"]), tuple(tools))
        ceiling = policies[name]
        ceiling.validate_grant(grant)
        policy = replace(ceiling, read=grant.read, write=grant.write, tools=grant.tools)
        supervisor.revise(task, policy)
        return {**supervisor.status(task), "template": name,
                "worker_proposal": json.loads(json.dumps(asdict(grant))),
                "planner_record": context.record(output).id}
    except (ValueError, TypeError, KeyError):
        supervisor.cancel(task)
        raise ValueError("Planning response is not a valid trusted-template proposal") from None
    except Exception:
        supervisor.cancel(task)
        raise
    finally:
        if context is not None:
            context.close()
        if store is not None:
            store.close()
