# Server admission and lead-in experiments

These overlays reproduce the P4 fairness experiment on the 2x2080 Ti
production box. They are a deployed candidate, not an upstream default and not
an unconditionally accepted configuration.

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
  --expect-engine-md5 8d680c695f1a6a3d9d202c61d17879f2 \
  --expect-env-file tools/server/production-env-2080ti.json
```

## Reproduce the workload

The candidate was measured by a one-hour mixed 2/3/4-way soak and by fairness
waves. It is not yet cleanly accepted: the 96K four-way wave was 2.53% slower
than the P2 baseline, while the `by_kind` single-request median was 4.14%
slower; an adjacent 400 ms D2 comparison was about 3.21% slower. Fix the
acceptance rule before the next run and keep both wave and per-request numbers.

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

The current one-hour evidence is:

- 606 requests and 202 waves, with zero HTTP/execution errors and 0 unexpected
  `length`;
- 25/25 96K needles correct and no swap use;
- 96K-neighbour code wall 41.61 s on P2 against 4.424 s on the candidate;
- 96K-neighbour short JSON wall 40.83 s against 3.370 s;
- 32K four-way wave 22.804 s against 22.188 s (+2.78%);
- 96K four-way wave 42.666 s against 41.613 s (+2.53%);
- 96K `by_kind` single-request median 42.658 s against 40.96 s (+4.14%).

Raw evidence:

```text
/opt/ai/logs/P4-candidate-mixed-soak-20261025T2320Z.jsonl
sha256 27a5d6f2efda807c49fb3480b155b0f256b95c9ef7df653a22545d1af1285f15

/opt/ai/logs/P4-candidate-mixed-soak-20261025T2320Z.summary.json
sha256 c46339838e17f9734ee645afbf02ef226480715a5c70a735ee95fb01262dc72d
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
