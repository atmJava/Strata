# UTIL-EXPDEC-GU contract

Status: rejected before restart by the startup-path gate. See
[`UTIL_EXPDEC_GU_RESULT-20261031.md`](UTIL_EXPDEC_GU_RESULT-20261031.md).

## Question

The source documents an opt-in `STRATA_EXPDEC=1` gate/up kernel. The proposed candidate was to
copy the current A config and add only that environment entry. The first gate was reachability:
both cards had to log the expected `expdec_gu_kernel` execution for gate/up, while down stayed on
the default path.

## Required path evidence

The running engine and source had to agree before any restart:

1. `STRATA_EXP_MODE` was `8`;
2. the model used `gu_type=18`, `n_embd=2560` and `n_ff=640`;
3. the mode-8 branch did not complete gate/up before `expdec_gu()`;
4. the mode-8 branch did not return from down before `expdec_down()`;
5. `STRATA_EXPDEC` was absent in A.

If any required gate/up call could not reach the EXPDEC kernel, the experiment was to stop before
creating a candidate or restarting the server.

## If reachable

A candidate would copy the A config and add only `STRATA_EXPDEC=1`. It had to start with six slots,
two groups, the same engine hash, health loaded and safe RAM/VRAM. Three frozen greedy probes would
run twice: each candidate answer had to match byte-for-byte and have the same finish reason as A.

Only after parity passed would the experiment run 2/4/6/8 streams with three repetitions. Adoption
required at least a 1% aggregate improvement at one or more target levels, no 2/4/6/8 median
regression above 1%, zero request errors or unexpected finishes, and unchanged slot, health,
resource and production-systemd guarantees.

## Reject gate

Reject before restart if the requested kernel is shadowed by an earlier mode-8 branch. Reject after
startup if a gate/up call does not take the EXPDEC path, down unexpectedly changes path, slots or
resources degrade, any parity probe differs, a performance median regresses beyond the gate, or
request integrity fails.

## Restart cost

One candidate start and one A restore would cost roughly 3-7 minutes of loaded-service interruption.
That cost was unnecessary once static reachability failed.
