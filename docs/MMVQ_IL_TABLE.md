# The interleaved small-batch matvec on sm_75

The decode and verify windows run their dense projections (2 to 4 tokens at a time) through `native_mmvq_il`, which reads
an interleaved copy of the q8_1 activations. Every choice gives the same bits as the plain kernels; only speed differs.
On sm_75 the path is on in code and token counts outside 2-4 keep the plain kernels; there is no switch.

How many matrix rows one warp takes (1, 2 or 4, or 0 = use the plain kernel) is `kIlRows_75` in
`src/kernels/cuda/native_mmvq.cu`. It is indexed by quantisation type (IQ4_XS, Q4_K, Q5_K, Q6_K, Q3_K), token count
(2-4) and five output-row classes: below 2048, 4096, 8192, 12288, and above 12288. The table was measured on an
RTX 2080 Ti 22 GB and runs because the card is sm_75; the per-switch overrides of earlier releases are gone.

## Retuning on the target cards

1. Build with tests: `cmake -S . -B build -DSTRATA_BUILD_TESTS=ON` and
   `cmake --build build --target mmvq_il_parity`.
2. Close other GPU work, then run `build/mmvq_il_parity --bench --emit-table`.
   It first checks bit-for-bit parity for every shape and rows choice, prints a `BENCH` line per shape, and finally a
   ready-to-paste `kIlRows_75` block.
3. Paste the block into `kIlRows_75` and rebuild. Each row value is 0, 1, 2 or 4; there is no runtime override.
4. A/B the two builds with the same flags, in interleaved pairs.

The emit rule is: pick the fastest rows count only when it takes at least 3% off the plain kernel; otherwise emit 0.
