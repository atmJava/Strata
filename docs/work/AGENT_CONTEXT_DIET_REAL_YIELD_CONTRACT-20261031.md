# AGENT-CONTEXT-DIET-REAL-YIELD contract

Date: 2026-10-31

Status: pre-registered before measuring the real-session yield of the existing
opt-in context diet.

## Why this is the current candidate

The eager deterministic context diet already passed three paired synthetic
eight-command waves:

```text
38.106 -> 32.914 s
58.307 -> 30.727 s
41.002 -> 32.686 s
```

That made the candidate opt-in, but it did not show how often real Codex
sessions contain foldable successful outputs, how much model-visible context
they retain across later turns, or whether the benefit survives real editing
and test loops.

At the same time, the real-session traces show that this is not a local GPU
micro-optimization question:

- 79,745,864 tool-output characters were found in the 26-file snapshot;
- outputs of at least 2K characters were 92.294% of that total;
- one 44,783-second root spent 31,088 seconds under tool spans versus 14,855
  seconds under inferred model spans;
- exactly-two-request model concurrency covered only 171.303 seconds in the
  latest 35-file snapshot.

The context diet cannot remove command runtime. It can remove repeated
model-visible copies of large successful outputs while keeping the exact
artifact available for rereading.

## Workload and privacy

The first stage is read-only and scans the stored Codex sessions. It may inspect
tool-output length and whether the proxy's fail-open success classifier accepts
the output. It must not emit or commit prompt, assistant, reasoning or tool-body
text.

For each tool output, report only:

- tool name and output size class;
- whether it is foldable by the current classifier;
- how many later model requests in the same session can see that history;
- a conservative token estimate and an absolute prompt-character reduction;
- the share of session input represented by repeated foldable output;
- compaction and abort boundaries;
- coverage gaps for non-string and custom tool outputs.

## Selection gate

Proceed to paired live runs only if the stored sessions give a conservative
whole-session model-visible reduction of at least 1%. The upper bound must count
first-appearance folding only; it may not assume that an artifact can be
discarded entirely.

The live gate uses the existing privacy-safe proxy and the same model, tools,
sampling, output limit, repository fixture and task prompt for control and diet
arms. Use at least three adjacent pairs or an A-B-A-B design if run-to-run
variance requires it.

Adopt the runnable opt-in path only if all conditions hold:

- edit, test and final-result correctness are unchanged;
- errors, failures, panics, tracebacks, denials and timeout output stay
  verbatim;
- every folded output can be recovered byte-for-byte from its artifact;
- the median paired task wall improvement is at least 1%, or at least two
  major prompt-heavy pairs clear 1% with no regressions;
- no increase in model turns, tool retries or early `stop` is hidden by a prompt
  reduction;
- the default direct-server path remains byte-identical.

## Stop rules

Do not restart Strata for this candidate. It changes the opt-in proxy only.

Reject the productionization if:

- fewer than 1% of model-visible input is conservatively removable;
- error classification loses or obscures a real failure;
- the model must perform an extra reread turn that erases the wall-clock gain;
- correctness or finish changes;
- the only measured gain is a synthetic prompt-token estimate.

## Follow-up structural candidate

Session files already exist, but the engine refuses them with `--layer-split`,
`--batch` and `--prompt-cache 0`. The current production shape uses a two-GPU
layer split and six batch slots. Cross-restart aged-session residency therefore
needs a separate feasibility contract before implementation; do not present the
current session-file API as a solution to the 113-second resume.
