# OpenCode connector boundary

The connector is pinned to **OpenCode 2.2.5**. It now includes an executable V2
Promise plugin, authenticated worker broker transport, explicit provider setup,
and no-task private-server bootstrap proposals. OS acceptance remains deferred
to Ubuntu. `Connector.check()` reports the exact installed release but keeps
production acceptance false until the remaining runtime checks pass; it no
longer represents the connector as merely a version probe.

## Implemented interfaces

`ConnectorConfig` selects an explicit `provider/model`, executable, version-probe
timeout, and optional `ProviderConfig`. The latter accepts an HTTPS provider URL
(or loopback HTTP fixture), credential environment name, context limit, and output
limit. Native OpenAI-compatible HTTP is the implemented provider path; LaaS needs
its actual endpoint/model and tool-calling verification. No endpoint is guessed.
Credentials must be explicitly supplied to `prepare`; unrelated environment
credentials are not inherited. Values never enter the JSON configuration or
object representations. Provider requests carry credentials normally, while
broker envelopes omit provider authentication headers.

`BridgeConfig` specifies the private loopback `/admit` endpoint, fresh bearer
token, broker timeout, and body size limit. The JavaScript plugin erases broker
configuration from its environment immediately after capturing it. Shell
creation removes the configured provider credential variables, all `PLANNER_*`
variables, and private OpenCode server passwords. Protection against reading the
parent process environment or memory remains part of OS acceptance; erasing
child variables alone does not prove credential isolation.

`prepare(...)` makes an inspectable `run --standalone` proposal with fresh
HOME/XDG/runtime directories, in-memory SQLite, disabled project/Claude config,
updates, snapshots, warming, formatters, and supplied plugin/provider config.
It never executes a task. Use `prepare_server(..., server_password=...)` for an
accepted launcher: it contains `serve --stdio --hostname 127.0.0.1 --port 0` and
no task prompt. The owner must retain the stdin lease, establish confinement,
and verify the actual bridge registration before submitting task data through
the private server API.

The pinned server's stdio mode removes its password environment variables and
uses stdin EOF as an ownership lease. This is useful lifecycle plumbing, not
proof that detached tools or every descendant die on supervisor loss.

## Hook bridge

`planner/opencode/bridge.mjs` loads directly as a local package without installing
a JavaScript dependency. It awaits supervisor admission before tool execution
and native provider dispatch. Requests use a single serialized sequence and an
authenticated bearer channel. Missing/invalid responses and transport loss close
the channel; ordinary denials fail the operation. There are no worker-facing
approval, policy installation, or recovery routes.

The initial `hello` reports the exact release and the hooks that actually
registered successfully. The Python broker requires all 17 names in
`REQUIRED_HOOKS`, then binds exactly one root session to the worker. Native
subagents are removed and other session IDs are refused.

| Boundary | Behavior |
| --- | --- |
| Prompt | Bound session and actual message ID are observed before model use. Attachments, skill selections, and agent mentions are refused until their resolution paths are covered. |
| Tools | `read`, `write`, `edit`, `glob`, `grep`, and `shell` pass their exact V2 arguments to the broker before effects. Unsupported tools are removed and refused. Native `write` reads existing contents, so it requires read and write authorization, like `edit`. |
| Permission evaluation | Reuses the checked tool ticket. Native permission prompts cannot become a separate user-approval channel or consume an exception twice. |
| Shell creation | Checks the bound worker and command/cwd; reports the requested timeout and strips control/provider credentials. A separate supervisor timeout/lifetime limit is not established here. Arbitrary shell filesystem access still requires OS enforcement. |
| Context | Primary, compaction, transient generation, and title hooks admit the actual assembled inputs. Origin claims inside content are not trusted. |
| HTTP | Captures the actual POST body after protocol lowering/raw overlays and before network dispatch. Captures response bytes before handing them to the driver. Credentials and headers are omitted from broker records. |
| Unsupported transports | WebSocket handshakes/send/receive and legacy AI SDK routes throw before use. Native encrypted/replacement-window compactions are also refused. |

HTTP bodies are bounded and retained only in ephemeral model context. The
response clone is buffered before the provider driver consumes it, so streaming
latency and memory overhead must be measured. This is not yet an optimization
for low-latency streaming.

MCP catalogs and external references are removed with transforms. Prompt, tool,
and HTTP hooks check the active plugin inventory and refuse additional external
plugins. These checks happen after plugin loading, so they do not replace
startup configuration isolation or OS confinement.

## Summary and message origins

The plugin uses actual `ctx.session.context` records to distinguish stored
assistant and completed compaction messages. A converted message's ID is bound
to the producing provider-response receipt; prompt IDs are bound to their
observed prompt receipts. The broker receives `message_sources` sidecars and
validates the receipts. It does not classify summaries by text markers or trust
metadata written by a model.

Ambiguous or missing generation-to-message mappings block instead of inventing
provenance. Ordinary plaintext compaction mapping has deterministic bridge/API
tests; full native compaction and every auxiliary path still need real harness
fixtures. Native replacement windows lack the same stable converted message IDs
and remain disabled. All propagated sensitivity and note persistence decisions
remain in supervisor Python, not in the plugin.

## Important bootstrap requirement

The pinned OpenCode source catches plugin import/setup failures and can continue
without that plugin. Merely setting a plugin path is therefore insufficient.
A rejected `hello` in a disposable fixture reproduced this behavior: the coding
harness continued and the dummy protected file was changed. That unsuccessful
attempt is retained in the fixture evidence.

A normal launcher must start without task data, verify authenticated registration
and plugin state, and only then admit the task. The OS grant must already apply
before this boot process. The ordinary `run` proposal is not an accepted shortcut.

## Source compatibility

The exact source tag was downloaded and inspected, rather than assuming current
web documentation matches the release. Relevant pinned source files are:

- [Promise hook shapes](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/plugin/src/promise/session.ts)
  and [tool shapes](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/plugin/src/promise/tool.ts).
- [Promise adapter](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/plugin/src/promise/adapter.ts):
  `tool.execute.before` errors become `Tool.Error`; session/after errors stop the
  flow. Plugin inventory has a location/data envelope; session context is an array.
- [Native lowering and transport hooks](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/session/model-request.ts).
- [Stored-message conversion](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/session/runner/to-llm-message.ts)
  and [message schemas](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/ai/src/schema/messages.ts).
- [Plugin activation](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/plugin.ts),
  [standalone ownership lease](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/cli/src/services/standalone.ts),
  and [stdio server options](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/cli/src/server-process.ts).

Stateless `experimental.generate.text/image/decisions` use
[`core/src/generate.ts`](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/generate.ts)
and bypass Session hooks. Generation tools and unknown external plugins are
disabled; the private server's stateless API routes and local control channels
must also be excluded or mediated before production acceptance. No hook-based
claim covers arbitrary untracked HTTP clients in additional plugin code.

## Validation and remaining acceptance

Run the deterministic Python/JavaScript seam tests with:

```sh
python3 -m unittest planner.tests.test_connector planner.tests.test_opencode_bridge -v
```

A fixed local diagnostic also runs the real installed binary, actual supervisor,
and authenticated broker against a deterministic loopback OpenAI-compatible
provider. It uses fresh dummy projects, no ambient credentials, and four cases:
permitted read, permitted write as a positive control, denial of the identical
write, and an unreviewed provider origin. It separately checks task completion,
actual effects, successful bridge registration, and admission of every observed
provider request. This prevents a successful task from masking a missing bridge.

```sh
python3 -m planner.opencode.diagnostic --output planner/evidence/opencode-http-fixture.json
```

The runner appends sanitized outcomes and source hashes; raw model bodies,
stdout/stderr, dummy secrets, and credentials are not exported. Failed attempts
remain visible. These are connector integration fixtures, not autonomous-agent
comparisons or OS confinement tests.

The latest recorded local attempt passes all four cases with the bridge active
and every observed provider request admitted. The denied-write case also
completes its permitted continuation. Six attempts are retained, including the
handshake mismatch and SDK response-shape failures that stricter checks exposed.
The final attempt records stable hashes for the bridge, runner, connector,
supervisor, broker transport, mediation, context, and provenance modules.

Production acceptance still requires no-task bootstrap verification, startup
system/managed configuration isolation, complete auxiliary/retry coverage,
private stateless-route control, sensitive harness-storage checks, and the Ubuntu
OS contract. In-memory SQLite alone does not prove that logs, caches, attachments,
truncated shell output, or other artifacts cannot persist sensitive content.
See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) for the deferred platform plan.
