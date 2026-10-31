# AGENT-PROFILE-HYGIENE result

Date: 2026-10-31

Status: adopted. The two production profiling variables are removed. A
long-output finish anomaly remains open and is not attributed to this change.

## Decision

Remove `STRATA_DECODE_TIMING` and `STRATA_VERIFY_PROFILE` from the live B4
configuration. On the stable 416 and 1,490 token classes, the unprofiled arm
reduced completion span by 6.61% and 5.38% against the A1/A2 adjacent median.
Both exceed the one percent user-visible gate. The 4,483 class was directionally
faster but its A1/A2 drift was 13.77%, so it is not used as adoption evidence.

The final production arm is B2, byte-identical to B. It has six slots, two
groups, context 262144, health loaded and neither profiling variable in the
engine environment.

## Arms

All arms used the same source, engine, model, sampling, prompt and six-slot
configuration. The only intended delta was:

```text
STRATA_DECODE_TIMING=1
STRATA_VERIFY_PROFILE=1
```

Arm sequence:

- A1: the existing instrumented process for the 416/1,490/4,483 replay.
- B: both variables removed, process `71658`, engine `71777`, loaded in 154 s.
- A2: both variables restored, process `73851`, engine `73858`, loaded in 145 s.
- B2: both variables removed again, process `75997`, engine `76004`, vision
  `75999`, loaded in 199 s.

## Result

Three single-stream waves per stable output class:

| max tokens | A1 span | A2 span | A adjacent median | B span | B reduction | A2 drift |
|---:|---:|---:|---:|---:|---:|---:|
| 416 | 4.240 s | 4.136 s | 4.188 s | 3.911 s | 6.61% | -2.45% |
| 1,490 | 14.347 s | 14.442 s | 14.395 s | 13.620 s | 5.38% | +0.66% |
| 4,483 | 45.451 s | 39.192 s | 42.322 s | 37.230 s | 12.03% | -13.77% |

The 4,483 row is reported but not used to claim profiler savings because the
instrumented arms themselves moved too much between processes. It is useful
only as a directional observation.

All 416 and 1,490 requests returned exactly the requested count with
`finish=length`. Their output hashes were unchanged across A1, B and A2:

- 416:
  `338c141243076b1481a87d49e254662cc7002efe13c7fa42cbfe466b4a793286`
- 1,490:
  `9c87f497f1c733287e6f80d3d4036c150d81fc79740a153c398053cdf6131c7e`

The 4,483 probe remains state-sensitive. A1's three requests all reached
4,483/`length`. B had two requests stop at 3,892 and one reach 4,483; A2 had
two stop at 3,892 and one reach 4,483. The early-stop hash was consistently
`79bc59829a4cef5ea815124895c671edee73ec59a36ade154972b1bcb8847ab4`
and the completed hash was
`21bb8246022e929cac6edb2f7db7724280a3e95c913b2c4d2e9fffc465fafbac`.
Because the anomalies exist in both instrumented and unprofiled arms, they are
not attributed to this configuration change. The all-`length` quality gate
still fails and remains an independent model/cache/MTP stability risk.

## Health and rollback

After B2:

- `/health` returned `loaded: true`;
- engine version `0.1.51`;
- `batch_slots=6`, `batch_groups=2`, context `262144`;
- selected runtime env still contains `STRATA_BATCH_SERIAL_TWO=1`,
  `STRATA_BATCH_SELF_COMMIT=1`, `STRATA_EXP_MODE=8` and
  `STRATA_SM75_PERF=1`;
- the engine environment does not contain either profiling variable;
- a 64-token post-deployment probe succeeded in `0.850 s`.

The config before the change is preserved as `a-config.json`; exact rollback
is to stop B2 and start the same source with that file.

## Evidence

Experiment directory:
`/opt/ai/logs/agent-profile-hygiene-20261031T082551Z/`.

- `a-config.json` SHA-256
  `7c90109272f6e2afb7c85d86224b886668259f81f739c737c9b7ad5b944d72dc`
- `b-config.json` SHA-256
  `4b396f441ef30f62ab5398cf7b18b3ed902175fd70db7a789ff9a9c14f7e0a5e`
- `b/decode-416.json` SHA-256
  `5eb102b8a42a5e184b3810fb27526859dd89a878f9ca865397942fe98d16c213`
- `b/decode-1490.json` SHA-256
  `2fdb3b191a70a6481823ae1f0456c4d78eb06b42b0c52af65c6844a33ec5b12a`
- `b/decode-4483.json` SHA-256
  `0bc1b28b254010145ef8417e853d939e1936c5dd53c877779bf0d468eb7f29ad`
- `a2/decode-416.json` SHA-256
  `3bea2e17a2ff95fab1604efcc8d02d3fda27c32f2443e657140ab8f13db32905`
- `a2/decode-1490.json` SHA-256
  `8bd16573e65c46321c2c5ca63011347d71a22e0413ba2ed86236e29f5c961cbd`
- `a2/decode-4483.json` SHA-256
  `c6a00d90d29be3eb9ee5b0c4f6c76e3fc255f39241484ef07f5e9487ad41de49`
- `final-smoke/decode-64.json` SHA-256
  `edb66121a3e1934295f8f6e90de6f2a718282d8cd07200595923ae2fa1c6fd37`

## Not measured

No two- or four-stream profiler A-B-A, mixed prefill/decode traffic, sampled
decoding, real tool-call task or long soak was run in this iteration. The
finish anomaly needs a separate trace with the same token stream and MTP/cache
state before it is called a model defect.
