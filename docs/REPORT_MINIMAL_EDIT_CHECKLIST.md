# Report corrections with minimal wording changes

**SUPERSEDED. Do not apply this checklist to Document (2).pdf. Use [REPORT_FINAL_EDIT_GUIDE.md](REPORT_FINAL_EDIT_GUIDE.md), the single current guide. Its exact replacement ranges include the remaining repairs without the F/T/A overlap below.**

This is the archived checklist for **Document (1).pdf**, reviewed on 20 November 2026. The material below records the earlier advice, not current editing instructions.

Source: `/Users/qang/Downloads/Chrome/Document (1).pdf` (10 pages), SHA-256 `99ee89e0481dc0433e7a3e43b2c6899eaf8a63f7d05cef2757e5d2917c2e3223`. Pages below refer to this version; the quoted text is the reliable search anchor after pagination changes. The PDF is unchanged.

## How to use this checklist

- **KEEP:** already correct, already fixed, or a writing choice worth preserving.
- **FIX:** incorrect facts or damaged text; apply the small replacement given.
- **ADD:** missing information needed to qualify the claims; insert only the short text given.
- **OPTIONAL:** presentation or wording preferences; not required for accuracy.

Apply the FIX items and ADD items. Keep everything else unless you want to change it. These are independent edits to the existing document, not instructions to rebuild it around a new academic-paper outline. Most replacements are one phrase or sentence. Longer replacements are reserved for text that is damaged or combines several incorrect claims.

## 1. Abstract and headings: one clear answer

**Keep your title and abstract. You have already added them. Do not add them again.**

The abstract is a short summary before the main report. The headings organize the main report. They do different jobs and do not conflict. You do not need to rename “Problem Statement” to “Introduction,” add section numbers, or use my previous proposed hierarchy.

Use this simple formatting map, keeping your existing order and wording:

| Text already in your report | What it is | Formatting action |
| --- | --- | --- |
| Cubicle: Task-Scoped Filesystem Authority for OpenCode Agents | Document title | Keep as the title |
| Abstract | Opening summary | Keep before Problem Statement; leave unnumbered |
| Problem Statement; Analysis; Technical Design; Implementation; Testing; Evaluation; Related Work; Limitations; Future Work; Conclusion | Main sections | Give these the same main-heading style |
| Idea; Policy; Planning and approval; The jail; The FUSE mediator; Hooks and model inputs; Applying changes; Stopping and recovery; Provenance and memory | Parts of Technical Design | Give these one consistent subheading style |
| Run 1; Run 2 | Parts of Evaluation | Keep as subheadings, adding dates as below |
| What this shows | Lead-in to the findings list | Keep as a bold lead-in or subheading; either is fine |
| References | New closing section | Add after Conclusion, using the main-heading style |

The diagram can remain within Technical Design. Abstract need not appear in a numbered hierarchy at all. Implementation and Testing currently look like body labels while several other headings are blue; fixing the style is enough. The duplicate Technical Design heading is already gone.

**KEEP your voice:** the cubicle analogy, office/manager/building-owner explanation, “Three things changed,” “Idea,” and “What this shows.” The opening “Coding agents think they know better” is a rhetorical choice, not a technical statement that this checklist requires you to remove. Correct the factual explanation that follows it.

## 2. Factual corrections and small qualifications

### F01 — Page 1, first paragraph: correct the native-rule example

**FIX only the sentences starting “For example” and ending “never during the runtime.”** Replace those sentences with:

> For example, using the Qwen 3.8 27B model through OpenCode 2.2.5, we tried to stop agents from reading .env files with OpenCode's native permission rules. In a separate deterministic test, the read tool was blocked, but an allowed shell command could still read the file. The rule is checked during execution, but it covers the harness's tools rather than every file operation inside the shell.

This keeps your example and straightforward tone. It removes the wrong `Read(.env*)`/`settings.json` syntax, the startup-only explanation, and the unsupported claim that the autonomous leaking run followed that exact sequence. [Implementation and controls: S1–S2 below.]

In the earlier sentence, change **“resulting in mid-task privilege escalation”** to **“resulting in actions outside the intended task permissions.”** A process can bypass a tool rule using authority it already has; that is not necessarily OS privilege escalation.

### F02 — Page 2, Analysis: make two small corrections and one addition

Change:

> In system security, we discovered that pledge() and unveil() are OpenBSD functions designed to address the problem of unwanted mid-task privilege escalation.

to:

> In system security, we discovered that pledge() and unveil() are OpenBSD functions designed to restrict a program's privileges.

Change **“subsystems of tools available for the program”** to **“classes of system operations available to the program.”**

Keep the rest of your explanation and cubicle analogy. Add at the end of the OpenBSD paragraph:

> Linux has a related mechanism, Landlock, which can restrict a process and its descendants. We do not use it in the current implementation; the FUSE section explains why.

Landlock is analogous to unveil, not an identical API. Reference the official manuals [R1–R3].

### F03 — Page 2, first design point: clarify which role must be deterministic

**OPTIONAL precision edit:** change **“The supervisor can't be an LLM.”** to **“The permission enforcer can't be an LLM.”**

Keep the rest of this point, including your office metaphor. It already explains that the planner is an LLM and the user approves. There is no need for the longer replacement paragraph I previously proposed.

### F04 — Page 3, Idea: limit the denial/request claim

Change **“the refusal becomes a request that the user can approve or refuse”** to **“the refusal can become a request that the user can approve or refuse.”**

Some operations, such as access to supervisor state, remain ungrantable. Keep the following sentences about replacement workers and one-open approvals; they are useful. [S3–S4.]

### F05 — Page 4, diagram caption: correct the trust boundary

Keep the first two sentences. Replace only:

> Everything outside it is trusted supervisor code or storage that the agent can't reach.

with:

> The supervisor and its storage are outside the agent's reach. The model provider and original project are shown separately; being outside the green box does not make them trusted supervisor components.

This is a caption correction. You do not need a new diagram. Enlarging the labels or using landscape is optional.

### F06 — Page 5, policy example: add the missing wildcard

Change:

```toml
deny_read = [".env", "**/.env*"]
```

to:

```toml
deny_read = [".env*", "**/.env*"]
```

This matches the experimental policy. Leave the other policy lines as they are. [S1, S5.]

In the preceding paragraph, change **“Explicit denials always win over allowances”** to **“Explicit denials win over ordinary allowances.”** Exact operator-approved exceptions are handled separately.

### F07 — Page 5, approval paragraph: add two missing features

After the sentence ending “needs the user,” add:

> Each delegated worker must stay within its parent's permissions. Before approving a sensitive read, the user is warned that the contents may be sent to the model provider and must explicitly confirm this. Approval cannot make the provider forget information already sent.

Then add:

> External inputs need an exact read approval. The worker receives a read-only copy of that file, not access to its parent directory or sibling files.

These are missing implementation details, not new features to implement. [S3–S4.]

### F08 — Pages 5–6, jail: label the profiles and startup limits

After your “three layers of Linux confinement” introduction, add:

> The AppArmor profile and seccomp filter are runtime-specific candidates tested on this Ubuntu guest.

Do not replace the three-layer explanation; repair its damaged lines using section 3.

After the repaired startup paragraph on page 6, add:

> This check verifies the required mechanisms, but not the Linux distribution or CPU architecture. Other hosts may pass the check without having acceptance results.

The existing Ubuntu-only support statement can stay. Prerequisite checking and platform acceptance are different. [S3, S6.]

### F09 — Page 6, FUSE versus Landlock: replace the misleading reason

Replace the paragraph starting **“We chose FUSE over simpler kernel rules such as Landlock”**, ending **“plain allow/de”**, with:

> We chose FUSE because its userspace mediator lets us handle each project-file operation, keep empty stand-ins, and turn denials into approval requests. Landlock also checks access as it happens, but its rules do not by themselves provide this approval workflow. Landlock is not used in the current implementation.

Keep your following three bullets, repairing “onc” to “once.” Do not call Landlock startup-only or imply it cannot enforce file permissions. [S6, R3–R4.]

### F10 — Page 6, hooks and provenance: qualify unsupported paths

In the opening sentence, change **“every tool call and every model request”** to **“supported tool calls and model requests.”** Repair the rest using section 3, then add:

> Unsupported tool, transport and context paths are blocked. Some native compaction paths remain disabled.

After “Any output inherits the origins of its inputs,” add:

> This records exposure and derivation, not which input caused the model's decision.

Keep your existing explanation of sensitive derived content; do not expand it into a claim about provider-side deletion. [S7.]

### F11 — Page 6, application: distinguish file replacement from a transaction

After the repaired sentence “Each file is replaced atomically, and the journal records progress,” add:

> The whole multi-file changeset is not one atomic transaction.

Keep your review/auto distinction and no-replay explanation. [S3.]

### F12 — Pages 6–7, stopping: add repeated-denial suspension

Add to the stopping paragraph:

> Repeating the same denied request also suspends the worker; the default limit is three identical denials.

Keep recovery and expired-permission wording, repairing only the damaged sentences below. [S3–S4.]

### F13 — Page 7, implementation: correct the size without rewriting the paragraph

Replace the damaged sentence starting **“The supervisor is about 6,800 lines”** with:

> The supervisor is about 6,450 lines of Python, mostly using the standard library (sqlite3, tomllib, argparse, subprocess).

Keep **“There are about 4,800 lines of tests.”** That rounded count is accurate. Add a short footnote or appendix note:

> Counts at revision 5aafb497: 6,455 physical lines in tracked non-test Python files and 4,769 in tracked Python tests, including comments and blank lines; helpers and vendored workload files are excluded.

This preserves your rough-count style instead of forcing exact counts and a long methods paragraph into the body.

### F14 — Page 7, CLI: replace the broken example only

The run-on example is incomplete, so replace that block with the small walkthrough in section 4 of this checklist. Keep your sentence “The whole flow runs from a command-line interface.” Do not add `--state` to the separate `planner.comparison` command: that entry point has different arguments.

### F15 — Page 7, Testing: avoid saying every test uses a real jail/model

Change:

> These tests run the real jail, real FUSE mount and real OpenCode with dummy secrets, rather than mocks.

to:

> The suite includes tests of the real jail, FUSE mount and OpenCode with dummy secrets, alongside policy and lifecycle tests. Some use a fixture provider; real-model runs are reported separately.

Keep the coverage list, repairing the damaged bullets. The recorded final counts are correct once “17 s” is repaired to “17 skipped.” [S8.]

### F16 — Page 7, review claims: change one word

Change **“All of their findings were fixed before the final suite run.”** to:

> All of their code findings were fixed before the final suite run. The difference in attempt-counting methods remains a documented measurement limitation.

No need to replace the entire review-history paragraph. [S8–S9.]

### F17 — Page 8, define the three actual comparison modes

Keep the introduction and replace just the three definitions with:

> 1. native: OpenCode's own permission rules, without the filesystem jail.
> 2. os: the FUSE-mediated workspace, Bubblewrap, AppArmor and seccomp, with checked application but without the OpenCode supervisor hooks.
> 3. supervisor: the same confined setup with tool and model-context mediation and provenance enabled.

Add:

> The os setup already uses supervisor infrastructure; it is not a bare kernel-only baseline. These modes compare groups of mechanisms, not each feature separately.

This corrects the truncated definition and prevents attributing all approval/lifecycle functionality only to the third mode. Keep all six scenario bullets. [S1, S5.]

### F18 — Page 8, scenario caveat: add two sentences

After the scenario list, add:

> The comment and memory cases placed text in workspace files; we did not establish that the model read it. The memory fixture also did not exercise the real saved-memory retrieval path.

Do not remove the scenarios or claim verified resistance to those payloads. [S5.]

### F19 — Page 8, tables: keep the numbers, introduce the dates and revisions

Change **“Run 1”** to **“Run 1 — 15 November 2026”**, and insert:

> Each scenario ran once in each setup, giving 18 runs. This first collection used code from before the independent-review fixes.

Change **“Run 2”** to **“Run 2 — 16 November 2026”**, and insert:

> We repeated the same 18-run matrix after the first review fixes, at revision c05bc7b. This was before the later review and pre-merge fixes; the final 246-test suite was recorded on 19 November.

Keep both tables and all current values. Change both **“Secrets sent to provider”** column headings to **“Dummy secret observed in provider traffic”** to match the recorded detector's scope. A footnote can retain the concise table header if width is a problem. Add the two evidence citations [S10–S11] below the corresponding tables.

### F20 — Page 8, findings: repair the attribution, not the whole list

Keep **“Every setup completed the legitimate task.”** Change the next sentence to:

> The protections didn't stop this repair task.

Replace only the bullet starting **“Native rules leak through the shell”** with:

> Native tool rules leave a gap for shell access, as the separate deterministic tests show. In the first documentation-injection run, a dummy secret appeared in native-mode provider traffic, but the record does not identify the exact command responsible. Across both collections, native disclosed in 1 of 12 runs and the confined setups in 0 of 24.

Keep the **“5 of 6” / “4 of 6”** bullet and append:

> These counts use different methods: native counts tool inputs naming sensitive paths, while supervisor counts recorded denials. They cannot be compared as attempt rates.

Keep the protected-write bullet. Its distinction between agent behavior and deterministic controls is already useful. [S10–S11.]

### F21 — Page 9, evidence strength: replace one sentence and add one

Keep **“With two runs per scenario per setup, these numbers support no statistical claim.”** It is adequate plain-language caution; no need to rewrite the whole paragraph.

Replace **“The guarantee comes from the deterministic tests, not from this sample.”** with:

> The deterministic tests provide evidence for the tested controls, not a guarantee for every environment.

Keep the timing caveat and add:

> These medians are not a controlled measurement of sandbox overhead.

### F22 — Page 9, Related Work: small replacements instead of a new literature review

Replace **“As far as we know, no shipped product combines everything this project does, although each piece exists somewhere”** with:

> Our design combines existing ideas from sandboxing, file mediation, approvals and provenance. Several systems overlap with it:

Replace the **“Coding agents with sandboxes”** bullet with:

> Coding agents with sandboxes. Existing coding agents provide OS sandboxes, but defaults and permissions vary. Codex uses a sandbox by default in its documented normal workflow. Our os setup is a local comparison, not a direct test of Codex's or Claude Code's sandbox.

In **“Agents working on a copy,”** replace only **“This limits damage but doesn't control what the agent reads.”** with:

> This limits damage; read access still depends on the files and resources exposed to the container.

In **“Research,”** remove the blanket sentence **“They work at the level of tool calls.”** Do not replace it with another blanket classification of all five papers. Qualify the final sentence to **“Our system also enforces project-file permissions beneath the agent's tools.”**

Add one new bullet:

> Nearby systems. ZTAC studies object-bound approvals for coding agents. ActPlane applies OS-level policies. KAIROS explores temporary kernel-enforced policy, while agentsh combines file mediation, approvals and audit. Our contribution is the specific OpenCode integration and tested policy and workflow semantics, rather than inventing those mechanisms.

Cite verified primary sources [R5–R9]. “Haven Harness” still needs a canonical source; do not fabricate its identity or publication status.

Replace **“We think the full combination isn't common for four reasons”** with **“These designs also have practical challenges.”** Keep the four short points as qualitative challenges, repairing the damaged first point. Change **“It is slower”** to **“Mediation can add overhead”**, since overhead was not measured here. Change **“Per-operation approvals tire users out”** to **“Too many approval requests can burden users”**, without claiming a user-study result.

### F23 — Pages 9–10, Limitations: retain your bullets, add the missing limits

Keep the **“Linux only”** lead-in and Ubuntu version. Repair macOS wording in section 3. Under **“Other Linux distributions: untested,”** add:

> The startup check does not verify the distribution or architecture.

After the repaired **“Speed”** bullet, add:

> We have not measured controlled overhead.

Change the lead-in **“Correctness is not checked”** to **“The supervisor does not check semantic correctness.”** The independent task evaluator does check the refund task; this avoids conflating it with enforcement.

Replace **“Small evaluation. Two runs per setup”** with:

> Small evaluation. Two runs per scenario per setup, with one worker model and different code revisions. The comment and memory fixtures did not establish exposure.

Add one final bullet:

> Missing measurements. Unnecessary approvals, false denials and aggregate recovery rates have not been measured. Recovery has passing controls, but not a reported reliability or latency distribution.

The trust-assumptions bullet also needs the repair below: FUSE and the helper are trusted enforcement components, not untrusted workers.

### F24 — Page 10, Conclusion: preserve the paragraph and narrow three phrases

Change **“Rules checked by the agent's own harness stop the agent's tools, not the agent: one shell command is enough to get around them.”** to:

> Rules checked for individual harness tools can leave a gap: an allowed shell command can perform file operations outside those tool rules.

Change **“we kept every task working”** to **“we kept this repair task working in all recorded runs.”**

Change **“a filesystem that checks every operation”** to **“a filesystem that checks project-file operations.”**

Keep the rest, but change **“The deterministic tests do show that, on the tested platform, an unauthorized read or write is refused before it happens”** to:

> The deterministic tests show the tested unauthorized reads and writes being refused before their effects on the recorded platform.

These small changes avoid a universal security claim without replacing your conclusion with a different voice.

### F25 — Page 10, future Landlock: remove an absolute guarantee

In the Landlock bullet, replace **“so a bug in the FUSE helper or a mount rule still can't let the worker out”** with **“which could add another kernel-enforced restriction if the mediator has a bug.”**

Keep the rest of the Future Work list after repairing its cut-off Windows sentence. Proposed extra testing stays future work; it does not need to be completed before fixing this report.

## 3. Restore damaged sentences, keeping the surviving wording

These are **text repairs**, not invitations to rewrite each section. The missing words cannot be recovered from the PDF alone; the completions below are reconstructed from the surrounding sentence and repository facts. If your editable original contains the intact sentence, use that instead, subject to section 2's factual corrections. Join broken words and restore spaces throughout.

Some rows also contain the small factual correction already identified above. Apply each row once; do not then replace its paragraph using the old E01–E51 plan.

| ID / page | Find this damaged text | Replace only that sentence or fragment with |
| --- | --- | --- |
| T01 / 5 | “AppArmor applies a profile generated code” | “AppArmor applies a candidate profile generated from the runtime code” |
| T02 / 5 | “path rules.The profile” | “path rules. The profile” |
| T03 / 5 | “seccomp blocks system calls that coulks: hard links and symlinks, ptrace,io_uring, bpf, and reading other processes' memory.” | “A candidate seccomp filter blocks selected system calls, including link creation, ptrace, io_uring, bpf, and cross-process memory access.” |
| T04 / 5 | “the worker only oken” | “the worker only receives a short-lived relay token” |
| T05 / 6 | “Before the user's prompt is sent, the rart is present” | “Before the user's prompt is sent, the runtime checks that each required part is present” |
| T06 / 6 | “The project inside the jail is a FUSE frivate copy.” | “The project inside the jail is a FUSE view of a private copy.” |
| T07 / 6 | “Every file operation goesthrough” | “Every file operation through the project mount goes through” |
| T08 / 6 | “It ahe operation comes from OpenCode's owntools, a shell command, a Python script or a detached background process.” | “It applies whether the operation comes from OpenCode's own tools, a shell command, a Python script or a detached background process.” |
| T09 / 6 | “A refused turned into a pending exceptionrequest.” | “A refused operation can become a pending exception request.” |
| T10 / 6 | Paragraph ending “a plain allow/de” | Apply F09's short paragraph replacement; keep the three bullets. |
| T11 / 6 | “allow a file to be opened exactly onc” | “allow a file to be opened exactly once” |
| T12 / 6 | “The supervisor must approve each one beforeo register with the broker before theprompt is sent, otherwise the run doesn't start.” | “The supervisor must approve each one before it proceeds. The plugin must register with the broker before the prompt is sent, otherwise the run doesn't start.” |
| T13 / 6 | “Any output inherits.” | “Any output inherits the origins of its inputs.” |
| T14 / 6 | “keep theorigins” | “keep the origins” |
| T15 / 6 | “marked sensitive and is never saved aut” | “marked sensitive and is never saved automatically as project memory” |
| T16 / 6 | “After the worker stops and cleanup is vilds a changeset from the private copy.It applies a change only if both of these hold” | “After the worker stops and cleanup is verified, the supervisor builds a changeset from the private copy. It applies a change only if both of these hold” |
| T17 / 6 | “the change is within the worker's cur” | “the change is within the worker's current write permissions” |
| T18 / 6 | “Each file is replanal records progress.” | “Each file is replaced atomically, and the journal records progress.” |
| T19 / 6 | “An interruptedapplication” | “An interrupted application” |
| T20 / 6 | “The supervisor then checks that tly what the user reviewed.” | “The supervisor then checks that the changes still match what the user reviewed.” |
| T21 / 6 | “While the worker runs, the supervisor ky.” | “While the worker runs, the supervisor keeps checking its authority.” |
| T22 / 6 | “or afailed check” | “or a failed check” |
| T23 / 7 | “itself dies, the jail and th After a crash, recover checks that allthe recorded processes” | “itself dies, the jail and the FUSE helper stop. After a crash, recover checks that all the recorded processes” |
| T24 / 7 | “Project notes (\"memory\") are stored sepA note can inform a later task but cannever grant access.” | “Project notes (\"memory\") are stored separately from permissions. A note can inform a later task but can never grant access.” |
| T25 / 7 | “are never created from sensitive content” | **KEEP.** This sentence is intact and does not need a replacement. |
| T26 / 7 | “An offline HTMLe real records, shows the graph oftasks” | “An offline HTML report, generated from the real records, shows the graph of tasks” |
| T27 / 7 | Sentence ending “6,800 lines of dard library” | Apply F13's one-sentence size correction. |
| T28 / 7 | “a C program for the FUSE mount, whicho the Python policy” | “a C program for the FUSE mount, which forwards operations to the Python policy mediator” |
| T29 / 7 | “246 tests: 229 passed, 0 failed, 17 s” | “246 tests: 229 passed, 0 failed, 17 skipped” |
| T30 / 7 | “one real-model test that has to be switch” | “one real-model test that has to be switched on explicitly” |
| T31 / 7 | “exceptions: single-use approvals, repd refused requests” | “exceptions: single-use approvals, replay attempts and refused requests” |
| T32 / 7 | “cancellation, the supervisor dying mi” | “cancellation, the supervisor dying mid-task, and recovery” |
| T33 / 7 | “repository configuration being ignorance” or “repository configuration being ignorence” | “repository configuration being ignored during worker startup” |
| T34 / 8 | “os: the same permissions enforced by” | Apply F17's complete three-item definition. |
| T35 / 8 | “misleading instructions in tool outpu” | “misleading instructions in tool output;” |
| T36 / 8 | “misleading instructions in a memory note” | Add the missing semicolon for consistency with the other bullets. |
| T37 / 9 | “Mediating every file operation needsok on each OS.” | “Mediating every file operation needs different support on each OS.” |
| T38 / 9 | “macOS: the Seatbelt sandbox fails 2it version needs Xcode, a signingidentity with a special entitlement, and user approval in System Settings.” | “macOS: the Seatbelt candidate fails 2 of 14 probes. The FSKit version still needs Xcode, a signing identity with the required entitlement, and user approval in System Settings; detached-process cleanup also needs a privileged helper.” |
| T39 / 9 | “Speed. The mediator is single-threade are slow.” | “Speed. The mediator is single-threaded Python, so large builds may be slow.” |
| T40 / 9 | “separate supervis workers” | “separate supervisor-launched workers” |
| T41 / 9 | “whether its are correct. An authorized edit can s” | “whether its edits are correct. An authorized edit can still introduce a bug.” |
| T42 / 9 | “It doesn't find secrets copie into files the agent is allowed to re” | “It doesn't find secrets copied into files the agent is allowed to read.” |
| T43 / 9 | “no which one the model actually relied o” | “not which one the model actually relied on.” |
| T44 / 10 | Damaged Trust assumptions bullet | “Trust assumptions. We trust the OS, supervisor, mediator, helpers and user. Kernel exploits, an administrator bypassing the system, or a user approving excessive access are outside the guarantee. Network containment beyond the model relay is also out of scope.” |
| T45 / 10 | “macOS support through FSKit, and a Wi a minifilter driver or ProjFS).” | “macOS support through FSKit, and a Windows backend to be evaluated, for example using a minifilter driver or ProjFS.” |

T45 lists candidate technologies, not accepted equivalents: ProjFS alone does not establish a security boundary. Do not claim a Windows design has been selected or tested.

Keep the remaining intact sentences. In particular, restore the lost text in your existing paragraph instead of substituting the old plan's entire new paragraph.

## 4. One replacement for the broken CLI block

Use a monospaced block. These commands are an operator walkthrough for the accepted Ubuntu VM, run from the standalone repository root. State that the listed runtime paths, provider credential and project must already exist. The helper/profile setup is in [CONNECTED_RUNTIME.md](CONNECTED_RUNTIME.md).

```sh
planner_state=/srv/planner/state.sqlite
planner_model=soclaas/qwen3.8:27b
planner_provider=https://soclaas-api.comp.nus.edu.sg/v1

planner_cli() {
  python3 -m planner --state "$planner_state" "$@"
}

planner_cli plan /work/project --prompt "Fix refund handling" \
  --model "$planner_model" --provider-url "$planner_provider" \
  --api-key-env SOCLAAS_API_KEY --timeout 120

# Replace TASK and REVISION below with the returned task ID and reviewed revision.
planner_cli review TASK
planner_cli approve-task TASK --revision REVISION --assign worker

planner_cli run TASK worker --prompt "Fix refund handling" \
  --model "$planner_model" --provider-url "$planner_provider" \
  --api-key-env SOCLAAS_API_KEY \
  --opencode /opt/opencode-2.2.5 \
  --fuse-helper /opt/planner/fuse-bridge \
  --runs /srv/planner/runs --run-seconds 1800 --timeout 120

planner_cli applications TASK
```

Keep the API key out of the report. Explicitly label TASK/REVISION as values the operator substitutes after review, not literal runnable arguments. This example uses the existing default auto-application mode; it does not require a new review/apply workflow just to explain your current flow. If space is tight, put the block in a commands appendix and keep a one-sentence pointer in Implementation. Moving it is optional; complete flags and clearly marked substitutions are necessary if the commands stay.

## 5. Three short additions for completeness

### A01 — After Evaluation: what the layers added and what is unmeasured

Add one short paragraph:

> Both confined setups had no observed disclosure, so these runs do not isolate an extra prevention benefit from provenance. The full supervisor adds tool and model-context admission and provenance records on top of the shared confined workspace. We have not yet measured unnecessary approvals, false denials, recovery rates or controlled overhead.

You do not need the two large contribution/measurement tables from my previous plan. They were optional ways of presenting these facts. Your existing abstract already mentions some of the missing metrics; this paragraph makes the body consistent with it. [S5–S6, S10–S11.]

### A02 — End of Testing: acknowledge failed attempts

Add:

> Not every development attempt passed. Earlier tests failed closed when a required profile was missing or hook registration timed out. A native comparison control also timed out twice for an unresolved reason, then passed in seven later runs. The retained evidence records these attempts and the fixes; they are separate from the 36 agent runs.

Cite [S8–S9]. If the detailed development history matters to your reader, the older plan's failed-attempt table can be an appendix, but it is not a mandatory new body section. Keep the final 229/0/17 suite result, linked to its revision. Do not imply that a successful later run erases earlier defects or that all historical collections ran on the final code.

### A03 — References after Conclusion

Add a single reference list, with citations beside the statements they support. The body does not need to expand into a separate research survey. Use only references you actually discuss, and attach the local results records to the tables.

Local evidence keys used in this checklist:

| Key | Source |
| --- | --- |
| S1 | [Comparison implementation](../planner/comparison.py) |
| S2 | [Forced read/write measurement controls](../planner/tests/test_comparison.py) |
| S3 | [Connected runtime](CONNECTED_RUNTIME.md) |
| S4 | [Operator policy and workflow guide](USAGE.md) |
| S5 | [Evaluation protocol and limits](EVALUATION.md), [frozen scenario manifest](../planner/scenarios/benchmark.json) |
| S6 | [Known limitations](KNOWN_LIMITATIONS.md) |
| S7 | [Pinned OpenCode hook coverage](OPENCODE.md), [provenance](PROVENANCE.md) |
| S8 | [Suite and retained failures](../evidence/completion-progress.json) |
| S9 | [Completion/review chronology](PLAN_COMPLETION.md) |
| S10 | [15 November collection](../evidence/completion-comparison.json) |
| S11 | [16 November repeat](../evidence/completion-comparison-repeat.json) |

External references, verified in the preceding primary-source review on 20 November 2026:

| Key | Primary reference |
| --- | --- |
| R1 | [OpenBSD pledge manual](https://man.openbsd.org/pledge) |
| R2 | [OpenBSD unveil manual](https://man.openbsd.org/unveil) |
| R3 | [Linux Landlock documentation](https://docs.kernel.org/userspace-api/landlock.html) |
| R4 | [FUSE interface](https://docs.kernel.org/filesystems/fuse/fuse.html) |
| R5 | [Official Codex approvals and security documentation](https://learn.chatgpt.com/docs/agent-approvals-security) |
| R6 | [ZTAC: Names Are Not Objects, arXiv:2610.14488v1](https://arxiv.org/abs/2610.14488v1) |
| R7 | [ActPlane, arXiv:2606.25189v2](https://arxiv.org/abs/2606.25189v2) |
| R8 | [KAIROS authors' repository](https://github.com/jmanoj0905/KAIROS) |
| R9 | [agentsh repository](https://github.com/canyonroad/agentsh) |

The nearby-system statements describe the authors' published designs, not independent comparative tests. Obtain the canonical Haven Harness source before citing it. Keep citations for any of the older papers you retain only after checking that their actual mechanisms support your wording. Pin moving repository sources for a final bibliography.

## 6. Optional changes — do not treat these as remaining defects

- Rename your sections or add numbering: **not needed**. Keep the map in section 1.
- Rewrite the abstract: **not needed**. It is already present and cautious about the results.
- Replace the cubicle/office metaphor: **not needed**.
- Add a second diagram, a 36-cell graphic or two more evidence tables: optional. A landscape diagram or larger labels may help readability, but the caption correction is the factual requirement.
- Smooth grammar such as “then later decided” to “then later decides,” or “Weaker models” to “Models”: small optional refinements. Model size alone is not an established explanation of susceptibility.
- Move commands or development-history detail into appendices: optional space-saving layout choices, not a new body hierarchy.
- Run 270 or 450 model trials: **a separate future study**, not required to repair this report. Keep the existing Future Work sentence about more models. The detailed protocol remains in [section 12 of the older plan](REPORT_REVISION_PLAN.md#12-new-multi-model-opencode-evaluation-on-the-vm), if you decide to run it later.

If you want one additional Future Work sentence, use:

> We will compare more SoC LaaS models on the VM, with repeated fresh runs and separate reporting of task completion, injection behavior, denied operations and provider errors.

Do not merge the earlier host-model experiments with the VM confinement results or describe the planned study as completed.

## 7. Final check and review status

1. Keep the title, abstract, unique Technical Design heading and two existing tables.
2. Apply the factual changes F01–F25 (F03 is optional) and the text-repair rows in section 3 once each; T25 explicitly says KEEP. Some entries point to the same sentence deliberately.
3. Add the short qualifications A01–A03 and nearby-system citations. Facts can be brief; new large tables are not required.
4. Keep the current main section names/order and make their formatting consistent.
5. Re-export and inspect all pages for missing words, bullets, clipped table cells and small diagram labels. Search the exported text for the quoted damaged fragments as a second check.
6. Verify code revision references and result counts against S8–S11. Do not imply the 16 November repeat included the 19 November fixes.
7. Ignore the earlier plan's mandatory-sounding title, abstract, renaming and wholesale paragraph replacements. Follow this checklist instead.

An independent subagent reviewed the latest PDF and the earlier plan. It found the technical audit mostly sound, but identified stale instructions, excessive rewriting and unclear required/optional boundaries. It then reviewed this replacement checklist, checked page anchors and table values, and suggested three small adjustments, all incorporated: F03 is optional, T25 preserves the intact memory sentence, and the repeat is described as an 18-run matrix. It found no other blocking gap. The title, abstract, unique Technical Design heading, result tables, metaphor and rounded test-line count are preserved. All ten source PDF pages were visually inspected by the primary reviewer; local reference targets and shell syntax were checked. No model trials or PDF modifications were performed for this review.
