# Native macOS backend assessment

Assessed 15 November 2026 against the shared permission requirements in
[PLAN_COMPLETION.md §9](PLAN_COMPLETION.md#9-approved-amendments) and
[PLAN_PLANNER.md](PLAN_PLANNER.md). This is a design assessment, not platform
acceptance evidence. No extension was installed or enabled, no OS settings were
changed, and no new enforcement probe was run during this assessment.

## Finding

Apple's FSKit provides a concrete native filesystem mediation candidate on this
host. A per-worker FSKit volume, restricted worker execution, and a separate
trusted lifecycle helper merit implementation and disposable acceptance tests.
Existing Seatbelt failures do not establish that native macOS support is
impossible. Conversely, FSKit documentation does not establish that this proposed
combination already satisfies the contract.

The next implementation step is a small FSKit app extension with the shared
Python policy broker, followed by a separately verified descendant-cleanup
design. Extension activation is an external setup step; the remaining policy,
alias, caching, lifecycle and integration work is engineering work.

## Observed local facts

- The host reports macOS 27.0.1, build 26A434, Darwin 27.0.0, arm64.
- `/usr/bin/sandbox-exec` and the system FSKit framework exist. Command Line
  Tools provides `/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk`; the
  inspected SDK headers include FSKit's new handler and cache-control APIs.
- The installed `sandbox-exec(1)` manual marks the command deprecated and directs
  app developers to App Sandbox. The existing diagnostic must remain a
  diagnostic until its replacement or amended profile passes acceptance.
- The latest retained attempt in
  [macos-candidate.json](../evidence/macos-candidate.json) ran 14 checks with two
  failures: a hardlink introduced externally during execution disclosed a denied
  inode, and a detached descendant survived owner exit. These are historical
  results from 10 November, not tests rerun for this assessment.
- Listing system extensions failed with `OSSystemExtensionErrorDomain error 1`.
  That failure does not establish which extensions are installed or available.

These observations do not prove an FSKit module can be built, signed, registered,
mounted or used by the real worker here. Those checks remain pending.

## Supported filesystem seam

Apple documents a passthrough filesystem implemented as an FSKit app extension.
Its source is an existing directory represented by `FSPathURLResource`, accessed
through a security-scoped URL; its filesystem is exposed at a separate mount
point. It requires the `com.apple.developer.fskit.fsmodule` entitlement. Apple's
setup instructions build and run the app, enable its File System Extension in
Settings, and then mount it. This supplies a documented route without selecting
a third-party kernel driver. [Apple's passthrough sample](https://developer.apple.com/documentation/fskit/building-a-passthrough-file-system.md)

For this host's macOS 27 APIs, use `FSVolume.Handler` and the related handler
protocols. Apple replaced the older operations protocols and added `FSContext`
caller user/group information to some operations. The context is not documented
here as a task/worker capability, so a caller UID alone must not be treated as
proof of the specific reviewed grant. [FSKit updates](https://developer.apple.com/documentation/updates/fskit.md)

Read and write data must pass through `FSVolume.ReadWriteHandler`. Do not expose
backing extents through kernel-offloaded I/O: Apple documents that such routing
is chosen when an item is instantiated and remains fixed for that item's
lifetime. [ReadWriteHandler](https://developer.apple.com/documentation/fskit/fsvolume/readwritehandler.md)

Caching needs explicit treatment. Conform to `DataCacheHandler` and return
`KernelCacheCoherencyType.noCache` on every open and upgrade in the first
prototype. Merely omitting the protocol allows kernel caching outside the
module's control. The documented protocol supports cache negotiation and
invalidation; it does not by itself prove revocation or exact-once enforcement.
Test repeated opens, existing descriptors, mappings and reads after permission
changes. [DataCacheHandler](https://developer.apple.com/documentation/fskit/fsvolume/datacachehandler.md)

## Concrete implementation contract

These are proposed requirements for the prototype, not established behavior:

1. Give each worker/revision its own mediated volume over supervisor-owned
   private backing storage. Restrict the worker before task dispatch so it can
   access the volume and documented runtime resources, but cannot open backing
   paths, original project paths, other workers' volumes or operator interfaces.
   The current broad diagnostic runtime allowances require review too.
2. Keep policy decisions in the existing Python supervisor. The native extension
   sends authenticated operation requests and fails closed on broker loss. Do
   not duplicate policy semantics in Swift or infer approvals from worker text.
3. Serialize namespace changes with authorization. For directory moves, validate
   both endpoints and every affected descendant against its new logical path.
   Track stable object identity and sensitivity across moves so a permitted name
   never launders protected bytes. Newly created paths use the same pattern
   evaluator as existing paths and final application.
4. Own the private namespace and backing tree exclusively. Do not use a live
   passthrough view of an externally mutable original. Resolve symlinks through
   the mediator; reject unsupported link operations explicitly. Defend against
   backing substitutions and identity changes even if normal workers cannot
   create them. Repeat the historical dynamic-hardlink attack against the
   proposed architecture with a positive control.
5. Model blind replacement separately from reading. Never serve old bytes to a
   write-only worker, including through partial writes, mappings, renamed files
   or aliases. Stage replacement bytes as a new object; preserve the trusted
   source baseline for application. Define behavior for omitted old ranges and
   unsupported metadata rather than silently manufacturing a readable old copy.
6. Copy approved external regular files through the supervisor without exposing
   their parents. Bind each input to worker, identity, revision, lifetime and
   sensitivity. Task-lifetime copies may use a read-only mediated namespace. A
   once-only read should use one explicitly defined broker delivery rather than
   an indefinitely reopenable mounted file. Exact-once writes must bind the
   staged edit and original-file application to the same approval journal.
7. Record decisions and observed effects, then connect the existing changeset,
   OpenCode bootstrap, provider transport and context boundaries. A mounted
   filesystem alone is not a complete model-connected workflow.

## Lifecycle remains a separate acceptance gate

The installed `launchd.plist(5)` manual states that normal job cleanup kills
remaining processes with the job's process-group ID. It therefore does not
establish cleanup for descendants that call `setsid()` or change groups. The
installed SDK's `sys/event.h` explicitly says `NOTE_TRACK`, `NOTE_TRACKERR` and
`NOTE_CHILD` are unsupported since macOS 10.5. A `NOTE_FORK` observer is not a
verified automatic descendant-containment mechanism.

A trusted helper must survive Python supervisor failure, revoke filesystem and
provider access, terminate detached descendants, and verify termination before
application. One engineering candidate is an exclusively allocated worker UID
with a separately privileged lifecycle helper; another requires a proven native
process-containment facility. Neither was implemented or validated here.
Identity allocation, privilege dropping, credential escape, fork races and helper
death all require explicit tests. Do not replace this gate with periodic PID
enumeration or process-group cleanup alone.

Endpoint Security is not an already-available shortcut. Apple requires its client
entitlement and user approval through Full Disk Access. Its authorization and
notification APIs would still require an operation-coverage and failure-mode
assessment before selection. [Creating an Endpoint Security client](https://developer.apple.com/documentation/endpointsecurity/es_new_client(_:_:).md)

## External prerequisites and remaining work

The concrete external prerequisite for the proposed FSKit route is registering
and enabling the completed app extension, as instructed by Apple. The user must
review that OS change. Building and signing the module must also be demonstrated;
this assessment did not establish that a particular developer identity or an
additional Apple entitlement approval is required or missing. Do not report such
an account requirement without checking the actual build and registration result.

If lifecycle engineering selects a dedicated UID and privileged helper, its
installation and account provisioning will be additional explicit prerequisites.
They are conditional design choices, not established requirements of FSKit.

Implementation can proceed with extension source, broker integration and
disposable protocol tests before activation. Real filesystem acceptance requires
an enabled module. Full native macOS acceptance additionally requires successful
and denied file/directory operations, blind writes, dynamic aliases, external
inputs, single-use operations, children/grandchildren, detached-child cleanup,
broker/supervisor/helper failure and real confined OpenCode execution. None is
cleared by this assessment. A Linux VM client would be a separate deployment
choice requiring user review, not evidence of native macOS confinement.
