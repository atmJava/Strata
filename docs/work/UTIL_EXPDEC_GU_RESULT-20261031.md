# UTIL-EXPDEC-GU result

Run: 2026-10-31 on the current 2×RTX 2080 Ti A instance. This was a read-only source and process
audit; no candidate was launched.

## Decision

**UTIL-EXPDEC-GU is rejected before restart by the startup-path gate.** In production experiment
mode 8, earlier native gate/up kernels shadow `expdec_gu()`, and the mode-8 down kernel returns
before `expdec_down()`. Adding `STRATA_EXPDEC=1` cannot route the target calls through the EXPDEC
kernels, so there was no reason to construct a candidate, run parity, or measure throughput.

## Running state

- source revision `c219fbcac2cc6d877590c3c1da4fb98028f4acf8`;
- engine sha256 `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
- config sha256 `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f`;
- server/engine/vision PID `529088/529112/529097`, health `loaded=true`;
- `STRATA_EXP_MODE=8` in both the process environment and config;
- `STRATA_EXPDEC` absent in the process environment.

The source controls show that mode 8 with `gu_type=18` first enters
`native_gu_fused_kernel<18>` when available, otherwise the mode-8 LDS gate/up branch. The
`expdec_gu()` call appears after both branches. The down path takes
`native_down_lds_kernel` and returns before `expdec_down()`. This is a direct control-flow
observation from the deployed source tree, not a timed candidate result.

## Not measured

No candidate start, loaded-state check, parity probe, throughput run, or resource comparison was
performed. The existing A process was left untouched, so there was no restart interruption and no
new performance number to attribute to EXPDEC.

## Evidence

- `/opt/ai/logs/util-expdec-gu-static-20261030T2008Z.txt`
  (sha256 `bc3ac9bfbbddc2f153118d984e62b296a77895e1a1ad0eb6c9cd2e35232c32b3`)

The evidence file records the source and running hashes, process/config experiment mode, and the
relevant deployed-source branches.

## Next question

Move to the measured eight-stream plateau (`104.4 tok/s`, below six streams at `133.2 tok/s`).
Audit the pipeline wave and final partial wave for duplicated work, replay, handoff, and padding
before paying another restart cost.
