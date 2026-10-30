# Server admission and lead-in experiments

## 当前状态（2026-10-26）

- 本目录的 1600 ms lead-in、750 ms 等待、`STRATA_ADMIT_SHORT_FIRST=2048` 已由同 boot
  A-B-A 采纳为当前生产配置，不再是候选。
- 当前生产 engine 为 `a09d78b13c39378d1532a024b983d286` / `0.1.51`；
  `STRATA_PF_WMMA`、`STRATA_PA_V2T`、`STRATA_PREFILL_TIMING` 均不在生产进程中。
- P6 prompt-attention v2 现已是默认。2026-10-31 用 `STRATA_PA_V2T=0` 回退 v1 复验，
  engine read/wall 改善 `1.814%/1.905%`，按用户 1% 门采纳当前默认（L-095）。
- P7 `STRATA_PF_WMMA=1` 的 A1 首个请求期间被用户中断，没有有效 JSONL/summary；状态为
  **暂停/待做**。恢复时必须从全新 A1 重跑，不能复用中断样本。

These overlays reproduce the P4 fairness experiment on the 2x2080 Ti
production box. They are a deployed configuration and not an upstream default.
The strict same-boot A-B-A below accepted them on 2026-10-26, with narrow
headroom on short-JSON wall and 96K long-request latency.

## Candidate

- Service environment: `STRATA_ADMIT_SHORT_FIRST=2048`, loaded by
  `strata.service.admit-short-first.conf`.
- Engine configuration: `STRATA_PIPED_DECODE_LEADIN_MS=1600`,
  `STRATA_PIPED_DECODE_LEADIN_WAIT_MS=750`,
  `STRATA_PIPED_DECODE_LEADIN_MIN_TOKENS=4096`.
- The 750 ms wait was already part of the adopted P3 configuration. The only
  new server switch is the 2048-token first-admission order.

The service drop-in matters because the engine configuration's `env` map is
inherited only by the engine child. `STRATA_ADMIT_SHORT_FIRST` is read by the
Python server at import time.

## Install on the server

Snapshot the service, launcher, server, config, and environment profile first.
Then copy the drop-in into place, reload systemd, and use the normal config
installer:

```sh
sudo install -d -m 0755 /etc/systemd/system/strata.service.d
sudo install -m 0644 strata.service.admit-short-first.conf \
  /etc/systemd/system/strata.service.d/admit-short-first.conf
sudo systemctl daemon-reload
/opt/ai/bin/use-config.sh /opt/ai/tmp/P4-leadin-1600-20261025T230000Z.json
```

`tools/server/production-env-2080ti.json` includes both the server and engine
switches, so the version probe gates the complete candidate:

```sh
python3 tools/server/probe-version-stamp.py --local \
  --base-url http://127.0.0.1:8080 --api-key "$MP_API_KEY" \
  --expect-engine-md5 a09d78b13c39378d1532a024b983d286 \
  --expect-env-file tools/server/production-env-2080ti.json
```

## Reproduce the workload

The configuration was measured by a one-hour mixed 2/3/4-way soak and by
fairness waves. The original old-engine candidate was not cleanly accepted:
its 96K four-way wave was 2.53% slower than P2 while the `by_kind` median was
4.14% slower. A later same-boot A-B-A on the repaired `0.1.51` engine fixed the
attribution: code wall fell from 41.1875 s to 5.074 s, short JSON was 3.980 s,
96K `by_kind` was +2.761%, and 96K wave was +2.202%. All arms had zero errors
and zero needle failures.

Run equal arms on the same machine boot. Keep the service switch and lead-in
consistent within each named arm, and verify the running environment after
every install. A boundary run must compare 1500, 1550 and 1600 ms rather than
choosing the fastest one-off sample.

```sh
python3 tools/server/mixed-soak.py --profile fairness --waves 5 \
  --out /opt/ai/logs/P4-ADMIT-A3-400-<UTC>.jsonl
python3 tools/server/mixed-soak.py --profile fairness --waves 5 \
  --out /opt/ai/logs/P4-ADMIT-B4-1600-<UTC>.jsonl
```

P4 accepts the candidate only if all of these hold:

- 41-token code request wall at most 10 s;
- 96K wall within 3% in both the predeclared wave view and the per-request
  `by_kind` median; do not switch definitions after seeing the result;
- short JSON wall at most 4 s;
- zero HTTP/execution errors, zero unexpected `length`, and all needle answers
  correct;
- at least one hour of mixed soak for stability, plus a same-boot boundary
  A-B-A for attribution.

The repaired-engine evidence is:

- 618 requests and 206 waves, with zero HTTP/execution errors, zero unexpected
  `length`, and zero needle failures;
- RSS reached a plateau after the initial working set, and swap stayed unused;
- strict A-B-A code wall: A1 41.173 s, B1 5.074 s, A2 41.202 s;
- strict A-B-A short JSON: 3.418 s, 3.980 s, 3.396 s;
- strict A-B-A 96K `by_kind`: 41.009 s, 42.092 s, 40.913 s (+2.761%);
- strict A-B-A 96K wave: 41.176 s, 42.098 s, 41.206 s (+2.202%).

Short JSON has only about 20 ms of headroom below the 4 s rule, and 96K has
about 0.24 percentage points of headroom below 3%. Do not widen the lead-in or
first-admission range without a new pre-registered latency bound.

Raw evidence:

```text
/opt/ai/logs/P4-candidate-mixed-soak-20261025T2320Z.jsonl
sha256 27a5d6f2efda807c49fb3480b155b0f256b95c9ef7df653a22545d1af1285f15

/opt/ai/logs/P4-candidate-mixed-soak-20261025T2320Z.summary.json
sha256 c46339838e17f9734ee645afbf02ef226480715a5c70a735ee95fb01262dc72d

/opt/ai/logs/P4-strict-B1-1600-admit2048-20261026T055653Z.summary.json
sha256 46808901f6c3639e635b1952d977e8efdced7098f697b43bc90f725b1e016cc8

/opt/ai/logs/T-probe-P4-strict-final-20261026T061107Z.json
sha256 26711a4cb5361f83d9e1c419d36ee2f664d976244d020c873fc4e800705b9f5a
```

## Roll back

Move the drop-in out of the active directory, reload systemd, and install the
400 ms config snapshot:

```sh
sudo mv /etc/systemd/system/strata.service.d/admit-short-first.conf \
  /opt/ai/tmp/P4-ADMIT-20261025T222500Z/admit-short-first.conf
sudo systemctl daemon-reload
/opt/ai/bin/use-config.sh \
  /opt/ai/tmp/pre-P3-ABA-20261025T213040Z/strata-022.json
```

The 1200 and 2000 ms arms were rejected: longer fixed windows did not make the
code request stable. The 1400 ms arm also returned to 41.808 s. Do not retry
these settings unchanged; test the 1500/1550/1600 boundary with a fixed
acceptance rule instead.
