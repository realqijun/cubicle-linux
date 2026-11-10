# OpenCode connector boundary

The inspected release is **OpenCode 2.2.5**. It is pinned in
`planner.connector.PINNED_VERSION`. This is a compatibility investigation, not
an accepted autonomous-execution release: `Connector.check()` blocks task launch
even when the binary matches. There is no setting that bypasses this result.

The installed Homebrew formula references
[the v2.2.5 source tag](https://github.com/anomalyco/opencode/tree/v2.2.5).
On this host, an isolated `--version` returned `opencode v2.2.5` and `run --help`
advertised `--standalone`, `--format json`, and explicit `--model`. No model call
or provider authentication was performed. The existing artifact collector uses
the user's normal OpenCode configuration; it is observational and must not be
used as the supervised connector.

## Implemented checks

`ConnectorConfig` requires an explicit `provider/model` identifier and accepts
the executable and version-probe timeout. `check()` uses a fresh temporary
environment, checks the exact release, closes inherited descriptors, and
returns a structured blocking reason. It never starts a coding session.

`prepare(runtime_root, prompt, project=...)` makes an inspectable launch proposal
with a resolved executable, a fresh runtime directory, private `--standalone`
server, isolated HOME/XDG locations, an in-memory database, disabled project
configuration and Claude configuration discovery, disabled model fetching and
updates, and disabled snapshots. It inherits no provider credentials. The
proposal is **not an execution API**; callers must establish connector and OS
protection before starting it. In-memory SQLite alone does not establish that
logs, caches, attachments, snapshots, or other session artifacts cannot persist
sensitive text.

## Remaining acceptance work

V2's [server plugin interface](https://opencode.ai/docs/build/plugins/server)
offers separate primary-context, compaction, transient-generation, title,
tool-before, shell-before, HTTP, and experimental WebSocket hooks. A hook on one
route does not establish coverage of the others. Generation outside a session,
raw body overlays, retries, native subagents, plugin registration order, and
provider transformations must also be checked against the pinned source and
with independent fixtures. No trusted bridge has been installed here, and a
completed-event stream cannot block effects or assign authoritative origins.

[Configuration discovery](https://opencode.ai/docs/config/) can include system
configuration and macOS managed preferences. Disabling project configuration
and changing XDG locations does not prove those routes are absent. Configuration,
plugins, MCP servers, instructions, credentials, and shared-server/control
channels must be inaccessible or independently disabled before accepting a
confined session. The `--standalone` proposal avoids intentionally attaching to
the user's unrelated background service.

Before enabling execution, demonstrate actual tool and provider-input
interception, source ID assignment before dispatch, worker-specific confinement
and delegation, supervisor-loss termination, and sensitive session persistence
controls. Freeze coverage fixtures and retain failed results. Until those pass,
the connector remains blocked on every platform. This does not complete the
plan's OpenCode deliverable.

For later LaaS integration, use the actual NUS endpoint, supported model ID,
and a tool-capable protocol after confirming their current contract. Do not
guess an endpoint or copy ambient credentials. OpenCode's
[custom provider configuration](https://opencode.ai/docs/providers/#custom)
documents endpoint and environment-credential declarations; these need explicit
review in the eventual confined runtime. This implementation does not claim
that LaaS authentication or tool calling has been tested.

## Validation

Run `python3 -m unittest planner.tests.test_connector -v` from the repository
root. Tests exercise missing/incompatible binaries, matching versions that
remain blocked, invalid configuration, and private proposal preparation without
inherited credentials. Fixture executables test the public preflight boundary;
they are not evidence of OS confinement or model interception.
