# UTIL-BATCH-SIDE-MIXER contract

Status: rejected on 2026-10-31 at the server throughput and drift gates. See
[`UTIL_BATCH_SIDE_MIXER_RESULT-20261031.md`](UTIL_BATCH_SIDE_MIXER_RESULT-20261031.md).

## Question

Can a narrow side branch in the production `batch_rec_` path overlap the GDN query/key/value
projection with its gate projection, and the QSA query projection with its K/V projection, for
two rows per window, and save at least 0.5% of the fastest measured `18.27 ms` stage
(`91.35 us`) without changing any output?

## Reach

This candidate is deliberately different from the rejected env-only
`STRATA_SIDE_MIXER=2` test. Production decode reaches
`batch_step -> run_slot_rows -> stage_batch -> capture_batch -> record_window`, and
`capture_batch` sets `batch_rec_=true`. The existing `side_proj` and `sidem` gates require
`!batch_rec_`, so they have zero production reach.

The candidate will add a default-off `STRATA_BATCH_SIDE_MIXER=1` path only when all of the
following hold:

- the verifier is recording a batch window;
- the window has exactly two rows;
- the current layer is GDN, or the current layer is QSA and only its query chain is moved;
- profiling and the older branch mechanisms are off.

At two rows, production selects `native_mmvq_il` for the IQ4_XS GDN qkv and QSA q
projections. The IQ3_S GDN gate and IQ4_XS/Q6_K K/V projections remain on `native_mmvq`.
One-row windows stay on the serial path: the previous ordinary-window screen regressed there.
The first production-shape screen measured two rows at `245.2223 us` on GPU 0 and
`217.3697 us` on GPU 1, but that graph incorrectly repeated both GDN and QSA work in all 18
layers. Three rows saved `150.7229-167.7696 us`; four rows saved `146.3039-174.1634 us`.
The first-pass gate required `182.7 us`; only two rows cleared it on both cards, with seven
wins in seven paired processes and every process above the gate. Three rows cleared it in
only two of seven GPU-1 processes; four rows cleared it in only one. This contract therefore
narrows to two rows and corrects the production mixture.

The branch must preserve the existing order:

1. quantize/interleave the layer input on the main stream;
2. fork the side stream;
3. launch GDN qkv on the main stream and gate on the side stream;
4. run the slot-local conv and a/b gates on the main stream;
5. join before recurrence reads `z`;
6. for QSA, put only the query projection and its row-local split/norm/RoPE/rotation on the
   side stream while K/V projection, append and other slot-local state work stay on the main
   stream;
7. join before the attention gate/apply path reads the queries.

No row is reordered. No slot state, recurrent state, KV append, sampling or finish policy moves
to the side stream.

## Performance gate before source integration

The previous harness timed `native_mmvq` and omitted the QSA query chain. It therefore did not
measure the production IL dispatch and cannot by itself justify a batch implementation.

`batch_side_mixer_bench` must compare the actual production mixture:

- GDN qkv: IQ4_XS through `native_mmvq_il`;
- GDN gate: IQ3_S through `native_mmvq`;
- QSA q: IQ4_XS through `native_mmvq_il`;
- QSA k/v: IQ4_XS and Q6_K through `native_mmvq`.

Run `T=2` with `STRATA_SM75_PERF=1` on both physical cards. Run seven independent processes
per card. The earlier `T=1..8` screen is the reason for this single width. Each process must
cover:

- direct serial versus side-stream execution;
- one captured 18-GDN-layer graph per arm;
- one captured 6-QSA-layer graph per arm;
- one captured 24-layer graph per arm in the model's GDN/GDN/GDN/QSA order;
- byte equality for every qkv, gate, q and k/v output after both direct and graph execution.

The source-integration gate is:

1. all direct and captured outputs are byte-identical;
2. the 24-layer production-mix graph saves at least `91.35 us` (0.5% of the stage) on at
   least one card;
3. that shape wins at least 5/7 paired processes;
4. the 18-GDN and 6-QSA components do not regress either card;
5. no CUDA, graph-capture or graph-launch error occurs.

A 0.5% local saving is the pre-server screen, not the end-to-end adoption bar. It is worth a
same-boot A-B-A only if the corrected absolute saving still leaves realistic room for at least
1% at one production stream level.

## Exactness gate after integration

If the benchmark passes, add only `STRATA_BATCH_SIDE_MIXER=1` to a deep copy of A's config for
the candidate. Before throughput:

1. start with six slots, two groups, health loaded, the expected environment and safe resources;
2. prove the two-row batch graphs capture and launch;
3. run three frozen greedy probes twice against A and twice against B, with byte-identical text
   and equal finish reasons;
4. confirm one-row direct windows take the original path.

Then run one same-boot A1-B-A2 matrix at 2/4/6/8 streams, three 400-token waves per level and
arm. Adoption requires:

- at least one stream level improves at least 1% against the combined A1/A2 median and wins at
  least 5/6 paired repetitions;
- every other level regresses by no more than 1%;
- A1-to-A2 drift is no more than 3%;
- zero HTTP/execution errors, zero unexpected finish reasons and zero foreign requests;
- six slots, health, RAM, VRAM and swap remain safe;
- production systemd, production config and remote user files remain unchanged.

If any gate fails, remove the candidate env and restore A. Do not scan nearby row thresholds,
stream counts, QSA subsets or event placement.

## Restart cost

The benchmark and candidate build are offline with respect to the running server. A server test
costs about 130-198 seconds per load and 8-10 seconds per stop, so it is entered only after the
captured production-shape benchmark and exactness gate show enough absolute headroom.

## Execution checkpoint (2026-10-31)

The corrected production-shaped screen passed on both cards. Seven independent processes per
card covered `T=2`, the 18-layer GDN-only order, the 6-layer QSA-only order, and the real
GDN/GDN/GDN/QSA 24-layer mixture. Every output was byte-identical and no CUDA or graph error
occurred. The 24-layer median saving was `177.0208 us` on GPU0 and `191.7025 us` on GPU1;
both cards won 7/7 paired processes and all seven processes per card cleared `182.7 us`.

Candidate B then started with six slots, two groups, health loaded, and only
`STRATA_BATCH_SIDE_MIXER=1` added to A's deep-copied config. Three frozen greedy probes ran
twice and all six were byte-identical with `finish=stop`. A two-slot probe showed both slots
decoding (`busy=2, decoding=2`) and two captured two-row windows, proving the production
`batch_rec_ && T==2` path was reached.

The A1 baseline loaded and passed frozen parity, but its second 6-way wave failed with
`dequant launch: unspecified launch failure`. The baseline server exhausted its restart
attempts; afterwards `nvidia-smi` listed both cards while CUDA enumerated only one. The same
A config then failed early with the one-GPU conversation-parking error. The host has not
rebooted since 2026-10-29 and non-interactive sudo is unavailable, so A1-B-A2 cannot finish.
No candidate throughput result exists and the candidate remains default-off and unadopted.

After a manual host reboot, stop `strata.service`, confirm CUDA sees and can initialize both
UUIDs together, then restart this contract from A1. Do not relax the matrix or replace it with
a one-card result because of the infrastructure failure.

## Final execution (2026-10-31)

The host reboot restored both devices. A1-B-A2 then completed on one boot with all 36 waves.
B changed 2/4/6/8-stream aggregate throughput by
`-2.755%/-6.766%/+0.378%/+4.969%` against the combined A median, with paired wins
`4/6, 1/6, 2/6, 5/6`. Two-stream A1-to-A2 drift was `-7.067%`. Only eight streams met both
the 1% gain and 5/6-win gates, while two and four streams exceeded the 1% regression limit
and two streams exceeded the 3% drift limit. The candidate is rejected. A2 is restored with
the original engine/config and no candidate environment variable.

## Safety

The remote frozen source tree is dirty and belongs to the user. Build the candidate from a
separate copy or an isolated build tree; do not reset, clean or overwrite that worktree. Do not
write an API key, token or private prompt into the repository or evidence.
