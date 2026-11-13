# Default-path progress, 2026-11-13

## Global goal

Make Strata's default path reliable and fast on the one deployment this fork serves: a Linux host with two RTX 2080 Ti
cards, the Swift IQ3_S pack and DSH agent traffic. Refactoring is useful only when it removes a real ambiguity, a
repeated cost or a way for the running configuration to drift from the code.

The order of work follows the measured wait:

1. **Admission** - choose the next request by work still left, preserve reuse across every control-line re-entry, and
   keep the low-concurrency policy in code.
2. **Reuse** - restore parked conversations and slot prefixes instead of reading long prompts again. The open 3,894
   token case is below the current 4,096-token parking floor and still needs its own measured decision.
3. **Decode** - improve this last; admission and reuse account for the severe tail.

Correctness, task success and recoverability come before speed. Normal work does not start or restart Strata. Tests
run only when they can establish a source-level contract without a GPU, and a service experiment needs a concrete
question that source and fake-engine tests cannot answer.

## Baseline this direction starts from

- Twelve real DSH requests spent 615 s waiting before the engine started and 114 s decoding.
- A 3,894-token second turn restored 0 tokens because the conversation cache minimum is 4,096.
- Two 512-token streams took 11.776 s with concurrent batch admission and 10.004 s with the measured two-stream
  serial policy.
- The cache held 11.68 GB across 12 slots; KV cost was 14,220 B per token, so cache capacity was not the first
  bottleneck.

## Completed in this round

1. Admission ranks new work by prompt tokens minus the prefix a usable slot already holds.
2. Solo promotion, staged wait and BYIELD re-entry refresh that reuse before asking for the control lines again.
3. A busy slot's prefix no longer makes a request look short when an empty slot is available.
4. Staged read is unconditional with the deployment's measured 2,048-token threshold and counts pending work, not
   the full prompt.
5. Two-stream serial admission is the default; a third request promotes the active stream back to batching.
6. Solo hand-back and the 30-second long-wait fallback are code constants with no production environment override.
7. Deployment-policy tests reject the retired server switches and the AST admission test rejects a future
   `_take_control` call that omits known reuse.
8. A park now checks whether its replacement fits beside pinned or borrowed conversations before dropping
   superseded copies. A refused capture therefore keeps the old reusable state.
9. A staged read that has returned BRDY is ranked as zero tokens left to read while it waits to be adopted.

The implementation starts at `6ab3c517`; the source changes in this round end at `80fc3705`. The validated tree is
the documentation commit `97925527`. The branch is local and not pushed or deployed.

## Validation

- `serve.test_parallel` and `serve.test_parallel_yield`: 45 tests passed.
- `serve.test_staged_protocol`, `serve.test_staged_wedge` and `serve.test_admit_order`: 27 tests passed before the
  staged-read work change.
- `serve.test_deployment_policy`: 13 tests passed after the final server change.
- The complete engine group passed after the cache change (`conversation_cache`, split failure, memory, file and
  save-reclaim tests).
- `serve.test_admit_order` and `serve.test_deployment_policy`: 26 tests passed after the staged-read change.
- Python compilation and `git diff --check` passed after every commit.
- A clean archive of `97925527` built successfully on the remote host in
  `/home/atm/strata-build-20261113.B1GbiW/build-cached` with CUDA 13.0.88, `sm_75`, Release and `-j2`; the build
  exited 0. The binary reports `strata 0.1.51`, is 61,700,352 bytes and has SHA-256
  `6cdf18f17fed93b8b340da262edf4d9baff7dbd851ff3f08c29b1193da424659`. This proves assembly and the version
  entry point only. `ctest`, CUDA runtime smoke and deployment were not run.
- Strata was not started, restarted or deployed in this round. `stash@{0}` remains untouched.

## Open decisions

- **Parking floor.** Lowering `--conversation-cache-min-tokens` from 4,096 to 2,048 would cover the measured 3,894
  token session, but the handover only proposes that change. It is a default policy change and needs a repeatable
  five-session run that proves session 4 reuse without evicting the long conversations.
- **Engine environment cleanup.** The tree still contains 549 `getenv("STRATA_...")` reads, many for kernels, parity
  tools or incident diagnostics. They are not all production policy. Remove a read only after tracing its real
  startup path and preserving a parity or deployment-policy test.
- **Decode.** Do not start decode tuning until admission and the parking-floor decision are closed.

## Next

1. Audit conversation-cache parking and history restoration for correctness that can be proven without a GPU.
2. Prepare the five-session comparison for the 2,048 floor, but do not run or deploy it merely to demonstrate activity.
3. Use the validated binary only when a concrete runtime question cannot be answered by source or fake-engine tests.
