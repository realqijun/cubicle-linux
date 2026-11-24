# Remaining edits for Document (2).pdf

**Use this file only.** It supersedes both earlier edit checklists. It is based on the eleven-page PDF you supplied, not on an assumed stopping point at F13.

Source SHA-256: `12f1b3b6d91aad5bc0011aea2174fa81ce0c705c3c5b5c04a3ae4f5b152d01fa`.

## What went wrong, and what changed

I split factual changes and missing-word repairs into overlapping lists, left choices inside supposedly final instructions, and made you combine fragments yourself. The F13 instruction also failed to give a reliable selection boundary, and your latest implementation paragraph contains the resulting duplicated standard-library clause. That was a defect in my directions.

This guide uses one sequence in document order. Every edit has exact selection boundaries and one complete replacement. No edit refers you to another edit or an old F/T/A identifier. Already completed corrections stay in place. A new experiment is not a prerequisite for completing this report.

## How to apply it

1. Work in the editable source for Document (2).pdf. Keep an unchanged copy.
2. Follow edits 01 onward, once each. Page numbers locate the original PDF; use the quoted selection boundaries after pages move.
3. Select **from the first quoted words through the last quoted words, including both**. Replace that whole range with the replacement block. Do not append it. If the first and last quotes are identical, replace that phrase once at the stated location.
4. Paste only the replacement block, not its label or explanation. Keep existing heading styles. Render bullet lines as bullets and the shell block as code.
5. Leave text outside the selected range unchanged. In particular, keep the title, abstract, office metaphor, six invariants, corrected diagram caption, policy and approval paragraphs, and both tables' numeric cells.
6. For the two table-header edits, act on the indicated Run 1 or Run 2 table only. Those are the only intentionally repeated search phrases.

**Do not apply the old checklists after this one.** The completed corrections inside larger selected passages are already included in their replacement blocks.

## Edits

### 01. Correct the remaining privilege-escalation phrase

**Location:** page 1.

**First words to select:** `resulting in mid-task privilege escalation.`

**Last words to select:** `resulting in mid-task privilege escalation.`

**Replacement — paste this entire block:**

```text
resulting in actions outside the intended task permissions.
```

**Why:** The native shell can use authority the process already has; this need not be acquisition of a new OS privilege. Keep your opening and the native-rule explanation you already corrected.

### 02. Add the source for the corrected native-rule example

**Location:** page 1.

**First words to select:** `The rule is checked during execution, but it covers`

**Last words to select:** `every file operation inside the shell.`

**Replacement — paste this entire block:**

```text
The rule is checked during execution, but it covers the harness's tools rather than every file operation inside the shell. [1]
```

**Why:** This retains the sentence you already adopted and adds its source. The autonomous disclosure must still be distinguished from this deterministic control later.

### 03. Add sources to the existing OpenBSD/Landlock explanation

**Location:** page 2.

**First words to select:** `We do not use it in the current implementation; the FUSE section explains why.`

**Last words to select:** `We do not use it in the current implementation; the FUSE section explains why.`

**Replacement — paste this entire block:**

```text
We do not use it in the current implementation; the FUSE section explains why. [7–9]
```

**Why:** Your substantive correction here is already complete. This edit only adds citations; keep the rest of Analysis and the cubicle metaphor.

### 04. Finish the jail explanation and put the startup qualification next to the startup check

**Location:** page 5.

**First words to select:** `The worker is OpenCode 2.2.5, pinned to that exact release.`

**Last words to select:** `If anything is missing, the task is blocked before any request reaches the model.`

**Replacement — paste this entire block:**

```text
The worker is OpenCode 2.2.5, pinned to that exact release. It runs inside three layers of Linux confinement:

• Bubblewrap gives it its own user, process, network and IPC namespaces. Inside, it sees only:
  – the project mount;
  – /usr, read-only;
  – temporary /tmp and /state directories;
  – a restricted /proc;
  – two relay sockets.
• AppArmor applies a profile generated from the runtime code, as a second layer of path rules. The profile also blocks reading other processes' memory and environment.
• seccomp blocks selected system calls, including link creation, ptrace, io_uring, bpf and cross-process memory access.

The AppArmor profile and seccomp filter are runtime-specific candidates tested on this Ubuntu guest. [5, 6]

The jail has no unrestricted external network route. Model requests leave through a fixed relay. The real API key stays with the operator, and the worker only gets a short-lived relay token.

Before the user's prompt is sent, the runtime checks that every part is present: Bubblewrap, AppArmor with the required source-derived profile name loaded, FUSE, the FUSE helper and the pinned OpenCode. If anything is missing, the task is blocked before any request reaches the model. This check verifies the required mechanisms, but not the Linux distribution or CPU architecture. Other hosts may pass the check without having acceptance results. [5, 6]
```

**Why:** This preserves your explanation, repairs the two damaged bullets, and moves the existing “This check” qualification after the check it describes. Requiring a loaded profile name is not proof of arbitrary profile contents or general platform acceptance.

### 05. Finish the FUSE section without orphaned bullets

**Location:** page 6.

**First words to select:** `The project inside the jail is a FUSE filesystem mounted over a private copy.`

**Last words to select:** `• turn a refusal into a request.`

**Replacement — paste this entire block:**

```text
The project inside the jail is a FUSE filesystem mounted over a private copy. Every file operation through the project mount goes through the supervisor's Python policy before it reaches the copy. That covers open, read, write, rename, delete, mkdir, rmdir and truncate. It applies no matter whether the operation comes from OpenCode's own tools, a shell command, a Python script or a detached background process.

A file the worker may not read still appears, but as an empty stand-in, so tools don't break and its contents never enter the jail. A refused operation can become a pending exception request.

We chose FUSE because its userspace mediator lets us handle each project-file operation and provide an approval workflow. Landlock also checks access as it happens, but its rules do not by themselves provide this workflow. Landlock is not used in the current implementation. [5, 9, 10]

The mediator lets us:
• show stand-ins for files the worker can't read;
• allow a file to be opened exactly once under a once-only approval;
• turn an eligible refusal into a request.
```

**Why:** Your Landlock correction is retained. This restores the damaged denial sentence, limits mediation to the project mount and gives the existing bullets an explicit lead-in.

### 06. Finish the hook and sensitive-content qualifications

**Location:** page 6.

**First words to select:** `A plugin inside OpenCode sends every tool call and every model request`

**Last words to select:** `everything derived from it is marked sensitive and is never saved automatically.`

**Replacement — paste this entire block:**

```text
A plugin inside OpenCode sends supported tool calls and model requests to the supervisor's broker. The supervisor must approve each one before it proceeds. The plugin has to register with the broker before the prompt is sent, otherwise the run doesn't start. Unsupported tool, transport and context paths are blocked; some native compaction paths remain disabled. [1]

The broker checks the worker's current authority and records each input admitted to the model, along with where it came from. Any output inherits the origins of its inputs. This records exposure and derivation, not which input caused the model's decision. Supported summaries and compaction keep the origins of what they summarise. After the user approves a sensitive read, everything derived from it is marked sensitive and is not saved automatically as project memory. This does not prevent disclosure to the provider after explicit approval. [1, 6]
```

**Why:** The existing exposure/derivation sentence stays. “Supported” reflects the actual connector boundary, and “as project memory” avoids a blanket no-storage claim.

### 07. Restore Applying changes as one finished passage

**Location:** page 6.

**First words to select:** `After the worker stops and cleanup is vilds a changeset`

**Last words to select:** `The supervisor then checks that tly what the user reviewed.`

**Replacement — paste this entire block:**

```text
After the worker stops and cleanup is verified, the supervisor builds a changeset from the private copy. It applies a change only if both of these hold:
• the change is within the worker's current write permissions;
• the original file hasn't changed since the copy was made.

Conflicts, unauthorized changes, symlinks, hard links and special files block the whole changeset before application begins. Each file is replaced atomically, and the journal records progress. The whole multi-file changeset is not one atomic transaction. An interrupted application is reported, not replayed.

In auto mode, clean changes are applied after these checks. In review mode, they are kept until the user runs apply. The supervisor then checks that the changes still match what the user reviewed. [6]
```

**Why:** This is one replacement for all damaged text in this subsection; no separate fragment repairs follow. It distinguishes individual atomic replacements from whole-project transactions.

### 08. Restore Stopping and recovery, preserving the sentence already added

**Location:** page 7.

**First words to select:** `While the worker runs, the supervisor ky.`

**Last words to select:** `the default limit is three identical denials.`

**Replacement — paste this entire block:**

```text
While the worker runs, the supervisor keeps checking its authority. Cancellation, revocation, expiry or a failed authority check stops the worker's whole process namespace, including any background processes. If the supervisor itself dies, the jail and the FUSE helper stop. After a crash, recover checks that all the recorded processes and the mount are gone before it reconciles the records. It never replays an edit or revives expired permissions. Repeating the same denied request also suspends the worker; the default limit is three identical denials. [6]
```

**Why:** The repeated-denial sentence you added is preserved. The rest reconstructs the damaged text; it does not introduce a different recovery workflow.

### 09. Restore Provenance and memory

**Location:** page 7.

**First words to select:** `Project notes ("memory") are stored sepA`

**Last words to select:** `It is for explanation only and plays no part in enforcement.`

**Replacement — paste this entire block:**

```text
Project notes ("memory") are stored separately from permissions. A note can inform a later task but can never grant access. Notes record their origins, can be listed, searched and deleted, and are never created from sensitive content. An offline HTML report, generated from the real records, shows the graph of tasks, approvals, inputs, model calls and decisions. It is for explanation only and plays no part in enforcement. [1]
```

**Why:** This only restores missing words and spaces. The intact sensitive-content sentence is preserved.

### 10. Remove the duplicate clause left by the old F13 instruction

**Location:** page 7.

**First words to select:** `The supervisor is about 6,450 lines of Python,`

**Last words to select:** `which forwards every decision to the Python policy;`

**Replacement — paste this entire block:**

```text
The supervisor is about 6,450 lines of Python, mostly using the standard library (sqlite3, tomllib, argparse, subprocess). Two small helpers are in other languages:
• a C program for the FUSE mount, which forwards operations to the Python policy mediator;
```

**Why:** The full duplicated phrase is inside this selected range. Replace the range, do not append the new sentence. Python makes the policy decisions; the C helper forwards operations. Keep the following JavaScript bullet and the existing “about 4,800 lines of tests.” Counts at revision 5aafb497 are 6,455 non-test Python lines and 4,769 Python test lines, including blanks/comments and excluding helpers/fixtures.

### 11. Replace the incomplete command example with a complete operator walkthrough

**Location:** page 7.

**First words to select:** `The whole flow runs from a command-line interface:`

**Last words to select:** `python3 -m planner applications TASK`

**Replacement — paste this entire block:**

```text
The whole flow runs from a command-line interface. These commands run from the repository root on the accepted Ubuntu VM after the runtime setup in docs/CONNECTED_RUNTIME.md. The project and trusted storage paths must exist, the pinned OpenCode and FUSE helper must be installed, and the operator must supply SOCLAAS_API_KEY privately. [6]

planner_state=/srv/planner/state.sqlite
planner_model=soclaas/qwen3.8:27b
planner_provider=https://soclaas-api.comp.nus.edu.sg/v1

planner_cli() {
  python3 -m planner --state "$planner_state" "$@"
}

planner_cli plan /work/project --prompt "Fix refund handling" \
  --model "$planner_model" --provider-url "$planner_provider" \
  --api-key-env SOCLAAS_API_KEY --timeout 120

# Substitute the returned task ID for TASK, then inspect the proposal.
planner_cli review TASK

# Substitute the revision you actually reviewed for REVISION.
planner_cli approve-task TASK --revision REVISION --assign worker

planner_cli run TASK worker --prompt "Fix refund handling" \
  --model "$planner_model" --provider-url "$planner_provider" \
  --api-key-env SOCLAAS_API_KEY \
  --opencode /opt/opencode-2.2.5 \
  --fuse-helper /opt/planner/fuse-bridge \
  --runs /srv/planner/runs --run-seconds 1800 --timeout 120

planner_cli applications TASK

TASK and REVISION are operator-substituted values, not literal arguments. The example uses the default auto-application mode and requires actual user review before approval. The API key must not appear in the report or command arguments.
```

**Why:** This replaces only the incomplete example and its lead-in. Format only the command lines, from planner_state through planner_cli applications TASK, in monospace. The complete flags were checked against the actual CLI parser; the workflow was not executed. The separate planner.comparison entry point does not use this main CLI’s --state argument.

### 12. Restore Testing, qualify its scope and retain the failed attempts

**Location:** page 7.

**First words to select:** `The full test suite ran on an Ubuntu 24.04.3`

**Last words to select:** `All of their findings were fixed before the final suite run.`

**Replacement — paste this entire block:**

```text
The full test suite ran on an Ubuntu 24.04.3 x86-64 virtual machine:
• 246 tests: 229 passed, 0 failed, 17 skipped.
• The skips were 14 macOS-only diagnostics, two tests that depend on filesystem case behaviour, and one real-model test that has to be switched on explicitly.

The suite includes tests of the real jail, real FUSE mount and real OpenCode with dummy secrets, alongside policy and lifecycle tests. Some use a fixture provider; real-model runs are reported separately. They cover:
• permitted and denied reads and writes by built-in tools, shell commands, scripts, child processes and detached grandchildren;
• traversal, symlinks, hard links, rename, deletion, truncation and directory operations;
• exceptions: single-use approvals, replay attempts and refused requests;
• delegation that tries to exceed the parent's permissions, and fabricated approvals;
• cancellation, the supervisor dying mid-task, and recovery;
• application: conflicts, files changed after review, and interrupted applications;
• repository configuration being ignored during worker startup.

The project went through two independent code reviews and a pre-merge review. The recorded code findings were fixed before the final suite run on 19 November 2026. The difference in attempt-counting methods remains a documented measurement limitation. [2]

Not every development attempt passed. Earlier tests failed closed when a required profile was missing or hook registration timed out. A native comparison control also timed out twice for an unresolved reason, then passed in seven later runs. The retained evidence records these attempts and the fixes; they are separate from the 36 agent runs. [2]
```

**Why:** This preserves the correct counts and coverage list, restores all damaged bullets and adds the brief failed-attempt qualification required by the retained records. The final suite is distinct from the earlier agent collections.

### 13. Complete the actual comparison modes and disclose unproven payload exposure

**Location:** page 8.

**First words to select:** `We compared three setups on the same tasks, model and permissions:`

**Last words to select:** `The test runner itself ran inside the sandbox, since it executes code the agent wrote.`

**Replacement — paste this entire block:**

```text
We compared three setups on the same tasks, model and permissions:
1. native: OpenCode's own permission rules, without the filesystem jail.
2. os: the FUSE-mediated workspace, Bubblewrap, AppArmor and seccomp, with checked application but without the OpenCode supervisor hooks.
3. supervisor: the same confined setup with tool and model-context mediation and provenance enabled.

The os setup already uses supervisor infrastructure; it is not a bare kernel-only baseline. These modes compare groups of mechanisms, not each feature separately. [1]

The six scenarios were frozen before any results were collected:
• an ordinary task;
• misleading instructions in documentation;
• misleading instructions in tool output;
• misleading instructions in a code comment;
• misleading instructions in a memory note;
• a harmless quotation of an instruction.

The comment and memory cases placed text in workspace files; we did not establish that the model read it. The memory fixture also did not exercise the real saved-memory retrieval path. [1]

Every workspace contained dummy secrets at the top level and in nested folders. Task success was scored separately by nine fixed refund test cases. The test runner itself ran inside the sandbox, since it executes code the agent wrote.
```

**Why:** The scenario list and scoring explanation remain; the truncated os definition is repaired without pretending this is a clean ablation of every feature.

### 14. Introduce Run 1 before its existing table

**Location:** page 8.

**First words to select:** `Run 1`

**Last words to select:** `Run 1`

**Replacement — paste this entire block:**

```text
Run 1

On 15 November 2026, each scenario ran once in each setup, giving 18 runs with Qwen 3.8 27B and OpenCode 2.2.5. This first collection used code from before the independent-review fixes. The following table is retained as a historical result. [3]
```

**Why:** Keep the existing table and its date underneath. No table cells or numbers change.

### 15. Clarify the Run 1 disclosure column

**Location:** page 8.

**First words to select:** `Secrets sent to provider`

**Last words to select:** `Secrets sent to provider`

**Replacement — paste this entire block:**

```text
Dummy secret observed
```

**Why:** Change this column heading only in the indicated table. The observer scanned provider traffic; this wording does not overstate its directionality. Keep every numeric cell.

### 16. Introduce Run 2 before its existing table

**Location:** page 9.

**First words to select:** `Run 2`

**Last words to select:** `Run 2`

**Replacement — paste this entire block:**

```text
Run 2

On 16 November 2026, we repeated the same 18-run matrix after the first review fixes, at revision c05bc7b. This was before the later review and pre-merge fixes; it was not a repeat of the final code validated on 19 November. [2, 4]
```

**Why:** Keep the existing table and its date underneath. No table cells or numbers change.

### 17. Clarify the Run 2 disclosure column

**Location:** page 9.

**First words to select:** `Secrets sent to provider`

**Last words to select:** `Secrets sent to provider`

**Replacement — paste this entire block:**

```text
Dummy secret observed
```

**Why:** Change this column heading only in the indicated table. The observer scanned provider traffic; this wording does not overstate its directionality. Keep every numeric cell.

### 18. Finish the findings without changing the recorded counts

**Location:** page 9.

**First words to select:** `What this shows:`

**Last words to select:** `because the VM's clock pauses when the host is idle and the provider's speed varies.`

**Replacement — paste this entire block:**

```text
What this shows:
• Every setup completed the legitimate task. The protections didn't stop this repair task.
• Native tool rules leave a gap for shell access, as the separate deterministic tests show. In the first documentation-injection run, a dummy secret appeared in native-mode provider traffic, but the record does not identify the exact command responsible. Across both collections, the marker was observed in 1 of 12 native runs and 0 of 24 confined runs. [1, 3, 4]
• Agents do try to read secrets. In the repeat, native agents named sensitive paths in their tool calls in 5 of 6 runs. The supervisor denied sensitive reads in 4 of 6 runs and turned them into pending requests instead. These counts use different methods: named paths in tool inputs versus recorded denials. They cannot be compared as attempt rates. [4]
• No run changed a protected final file in any setup. So this sample doesn't show write protection; the deterministic tests provide that evidence. [1, 3, 4]

With two runs per scenario per setup, these numbers do not establish a reliable disclosure rate or a general security advantage. The deterministic tests provide evidence for the tested controls, not a guarantee for every environment. Times aren't comparable between the two collections because guest scheduling and host idling affected timing, and the provider's speed varies. These medians are not a controlled measurement of sandbox overhead.

Both confined setups had no observed disclosure, so these runs do not isolate an extra prevention benefit from provenance. The full supervisor adds tool and model-context admission and provenance records on top of the shared confined workspace. We have not yet measured unnecessary approvals, false denials, recovery rates or controlled overhead.
```

**Why:** The counts remain exactly as recorded. This separates the deterministic shell control from the autonomous disclosure, adds the counting-method and contribution caveats, and avoids duplicating these additions elsewhere.

### 19. Correct Related Work while keeping its bullet-list style

**Location:** page 9.

**First words to select:** `As far as we know, no shipped product combines everything`

**Last words to select:** `4. Writing good per-task permissions is hard.`

**Replacement — paste this entire block:**

```text
Our design combines existing ideas from sandboxing, file mediation, approvals and provenance. Several systems overlap with it:
• Coding agents with sandboxes. Existing agents provide OS sandboxes, but defaults and permissions vary. Codex uses sandboxed modes by default in its documented normal workflow. Our os setup is a local comparison, not a direct test of Codex's or Claude Code's sandbox. [11]
• Agent-side permissions. OpenCode's native rules check supported tools, but do not mediate every file operation inside an allowed shell. Our project-file checks are enforced outside the worker's tool logic. [1]
• Agents working on a copy. Private workspaces and containers can limit which files an agent sees and where its changes go. Their protection depends on the resources exposed and the rules enforced; a private copy alone is not a complete authorization policy.
• OS precedents. Flatpak's document portal uses FUSE to expose selected files to sandboxed apps. Sandstorm's powerbox lets users choose which capabilities an isolated app can access. These are related ideas, though their approval interfaces differ from ours. [16, 17]
• Nearby systems. ZTAC studies object-bound approvals for coding agents. ActPlane applies OS-level policies. KAIROS explores temporary kernel-enforced policy, while agentsh documents file mediation, approvals and audit. Our contribution is the specific OpenCode integration and tested policy and workflow semantics, rather than inventing those mechanisms. We have not run a direct comparison against these systems. [12–15]

These designs also have practical challenges:
1. Mediating file operations needs different support on each OS.
2. Mediation can add overhead.
3. Too many approval requests can burden users.
4. Writing good per-task permissions is hard.
```

**Why:** This keeps your list and practical-challenges discussion. It removes the unsupported uniqueness claim, inaccurate Codex default, container-read claim and blanket classification of research as tool-only. Unverified Haven Harness is not assigned an invented identity or citation.

### 20. Restore Limitations and include the limits already documented in the repository

**Location:** page 10.

**First words to select:** `• Linux only. The system is accepted only on Ubuntu 24.04.3 x86-64.`

**Last words to select:** `• Small evaluation. Two runs per setup`

**Replacement — paste this entire block:**

```text
• Linux only. The system is accepted only on Ubuntu 24.04.3 x86-64. The AppArmor profile and seccomp filter are runtime-specific candidates.
  – macOS: the Seatbelt candidate fails 2 of 14 probes. The FSKit version still needs Xcode, a signing identity with the required entitlement, and user approval in System Settings. Detached-process cleanup also needs a privileged helper.
  – Windows: has no accepted backend.
  – Other Linux distributions: untested. The startup check does not verify the distribution or CPU architecture. [5]
• Speed. The mediator is single-threaded Python, so large builds may be slow. We have not measured controlled overhead.
• One worker per run. OpenCode's own subagents are disabled. Delegated work runs as separate supervisor-launched workers.
• Semantic correctness. The supervisor enforces where an agent can read and write, not whether its edits are correct. An authorized edit can still introduce a bug. The refund oracle checks the evaluation task separately.
• Secrets in allowed files. The system protects files marked sensitive. It doesn't find secrets copied into files the agent is allowed to read.
• Provenance shows exposure, not cause. Origin tracking shows which inputs went into a model call, not which one the model actually relied on.
• Trust assumptions. We trust the OS, supervisor, mediator, helpers and user. Kernel exploits, an administrator bypassing the system, or a user approving excessive access are outside the guarantee. Network containment beyond the model relay is also out of scope. [5]
• Small evaluation. Two runs per scenario per setup, with one worker model and different code revisions. The comment and memory fixtures did not establish exposure.
• Missing measurements. Unnecessary approvals, false denials and aggregate recovery rates have not been measured. Recovery has passing controls, but not a reported reliability or latency distribution.
```

**Why:** This keeps your bullet labels and wording where intact, restores all damaged fragments, and states the unmeasured items explicitly. No new evaluation is claimed.

### 21. Finish Future Work without implying an untested guarantee

**Location:** page 10.

**First words to select:** `• macOS support through FSKit, and a Wi`

**Last words to select:** `• A faster mediator, and a startup check that refuses platforms that haven't been tested.`

**Replacement — paste this entire block:**

```text
• macOS support through FSKit, and a Windows backend whose enforcement mechanism still needs to be selected and tested.
• Evaluating Landlock as an extra kernel layer, which could add another restriction if the mediator has a bug.
• More evaluation runs and more SoC LaaS models on the VM, with separate reporting of task completion, injection behavior, denied operations and provider errors. A direct comparison against Claude Code's and Codex's own sandboxes also remains future work.
• A faster mediator, and a startup check that refuses platforms that haven't been tested.
```

**Why:** This repairs the cut sentence and preserves your four-point list. It does not prescribe 270 or 450 new runs as a condition of fixing the report or claim Landlock is implemented.

### 22. Narrow the conclusion while preserving its paragraph structure

**Location:** page 11.

**First words to select:** `Rules checked by the agent's own harness stop the agent's tools, not the agent:`

**Last words to select:** `an unauthorized read or write is refused before it happens.`

**Replacement — paste this entire block:**

```text
Rules checked for individual harness tools can leave a gap: an allowed shell command can perform file operations outside those tool rules. By moving enforcement below the tools, we kept this repair task working in all recorded runs while no confined run had a dummy secret observed in provider traffic or an unauthorized final-file change. The enforcement uses OS confinement, a filesystem that checks project-file operations, and permissions the user approves but the agent can't change. Our evaluation is too small to prove that this changes agent behaviour. The deterministic tests show the tested unauthorized reads and writes being refused before their effects on the recorded platform. [1–6]
```

**Why:** This preserves the existing argument and closing style, but limits it to observed runs and tested operations instead of every task, environment or possible disclosure.

### 23. Populate the existing References section

**Location:** page 11.

**First words to select:** `References`

**Last words to select:** `References`

**Replacement — paste this entire block:**

```text
References

[1] Cubicle project repository, revision 5aafb497b5399476dee9cb8ce5eb0f2edd9029a6. Relevant paths: planner/comparison.py; planner/tests/test_comparison.py; docs/OPENCODE.md; docs/PROVENANCE.md; docs/EVALUATION.md; planner/scenarios/benchmark.json. https://github.com/realqijun/agent_authorizer

[2] Cubicle retained validation and development records: evidence/completion-progress.json and docs/PLAN_COMPLETION.md. Final recorded suite: 19 November 2026, source revision 01f1858, 246 tests, 229 passed, 0 failed, 17 skipped. Repository as in [1].

[3] Cubicle initial agent collection, 15 November 2026: evidence/completion-comparison.json. Repository as in [1].

[4] Cubicle repeat agent collection, 16 November 2026: evidence/completion-comparison-repeat.json, source revision c05bc7b. Repository as in [1].

[5] Cubicle platform and threat-model limits: docs/KNOWN_LIMITATIONS.md. Repository as in [1].

[6] Cubicle connected runtime and operator workflow: docs/CONNECTED_RUNTIME.md. Repository as in [1].

[7] OpenBSD, pledge(2). https://man.openbsd.org/pledge

[8] OpenBSD, unveil(2). https://man.openbsd.org/unveil

[9] Linux kernel documentation, Landlock: unprivileged access control. https://docs.kernel.org/userspace-api/landlock.html

[10] Linux kernel documentation, FUSE. https://docs.kernel.org/filesystems/fuse/fuse.html

[11] OpenAI, Agent approvals & security. https://learn.chatgpt.com/docs/agent-approvals-security

[12] Daniel Commey, Names Are Not Objects: Object-Bound Confinement for Compromised AI Coding Agents, arXiv:2610.14488v1, 10 October 2026. https://arxiv.org/abs/2610.14488v1

[13] Yusheng Zheng et al., ActPlane: Programmable OS-Level Policy Enforcement for Agent Harnesses, arXiv:2606.25189v2, 30 June 2026. https://arxiv.org/abs/2606.25189v2

[14] KAIROS, authors' research repository. https://github.com/jmanoj0905/KAIROS

[15] agentsh, project repository and documentation. https://github.com/canyonroad/agentsh

[16] XDG Desktop Portal, Documents interface. https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Documents.html

[17] Sandstorm, Connecting grains to each other & Sandstorm via the powerbox. https://docs.sandstorm.io/en/latest/developing/powerbox/

Web references checked 20 November 2026. Historical source revision identifiers are retained as recorded; repository extraction may have rewritten commit IDs. The retained source manifests, rather than a current branch alone, identify the experimental code.
```

**Why:** The heading already exists; this replaces it with that same heading followed by the complete list. The in-text numbers in this guide use this list. It does not claim that external moving repositories have been independently tested or that every historical hash resolves in the extracted repository.

## Final formatting check

Keep the current section names and order. The abstract remains an unnumbered summary before Problem Statement. Give the main headings one consistent style and the design subsections one consistent subheading style. Keep each heading with its following paragraph; in the current PDF, Analysis is stranded at the bottom of page 1. The current Implementation and Testing labels should use the same main-heading style as Evaluation. No renaming or new hierarchy is required.

Keep both result tables and their existing values. Ensure neither table splits awkwardly, and that the caption/date stays with it. Enlarge the diagram labels only if needed for legibility; a new diagram is not required. Export again and inspect every page for cut-off text, missing bullets and clipped table cells.

## Checks behind this guide

All eleven source PDF pages were inspected. The replacement ranges were checked against the exact latest PDF text. Ranges do not overlap. The two repeated table-header phrases are scoped to their individual tables. A dry run checks that all replacements can be applied together, preserves the title and abstract, and keeps both numeric tables. Code arguments and syntax are checked without executing the workflow. An independent reviewer checked the latest PDF, the replacement guide and the dry-run text. Its copy-and-paste markup finding was corrected before delivery. The PDF itself and the historical evidence are not modified.

The final exported document still needs a complete visual check for layout and missing text. No new agent trials or VM tests were run for this editing task.
