# AGENT-COMPACTION-REAL-SHAPE contract

Date: 2026-10-31

Status: complete. Result:
[`AGENT_COMPACTION_REAL_SHAPE_RESULT-20261031.md`](AGENT_COMPACTION_REAL_SHAPE_RESULT-20261031.md).

## Problem

The synthetic 180K Codex compaction replay reused `180,272 / 180,301` tokens
(`99.984%`), but a frozen real-session trace measured only one compaction with
at least 50% cache reuse out of 44. The other 43 had a median input of
`283,884`, cached `7,424` and uncached `277,803`.

Across that trace, compaction was `11,902,305` uncached tokens, or `54.32%` of
all uncached input and about `1,186 s` of inferred model time. Removing half of
that model span would be about `1.09%` of the complete root wall union.

The client trace does not prove that its requests reached this Strata server.
Its token counts are workload evidence, not server timing. The first goal is
therefore to identify the real prompt-shape divergence, not to assume the
existing cache is broken.

## Stage 1: privacy-preserving diagnosis

Do not record or commit prompt, tool, assistant or reasoning bodies. For a real
Codex turn followed by its compaction, record only:

- whether Codex metadata identifies `request_kind=compaction`;
- stable hashes of the session and thread keys, never the raw IDs;
- whether the server used request tools or kept conversation tools;
- prompt token count and the common token prefix with the preceding turn;
- the first divergent structural boundary: message index, role, content kind,
  tool block presence, or template section;
- hashes of the system/developer block, tool JSON and history item kinds;
- rendered prompt character and token counts;
- engine `reused`, `prompt_ms`, decode time, wall time and finish;
- whether the request fell back because the kept-tool prompt exceeded context.

The diagnosis must cover at least three real compactions or two real
compactions plus one deterministic synthetic fixture built from the same
observed structural divergence. If raw request capture is unavailable, the
tool must say so and must not reconstruct content from hidden logs.

## Candidate rule

Only implement a prompt-preserving cache alignment after identifying a
content-identical prefix that the current path misses.

The candidate must not:

- rewrite or summarize client history;
- add tools to the model-visible compaction semantics;
- drop reasoning, user, developer or tool-result content;
- change the compaction instruction;
- lower output quality to gain cache reuse.

An opt-in server-side layout change is acceptable only if a deterministic
fingerprint proves the prior and compaction prompts share the same prefix
through the selected boundary. Any request that cannot prove that invariant
falls back to the request as sent.

## Measurement and adoption

Before a server reload:

1. The focused Responses/compaction tests pass.
2. Default-off prompts remain byte-identical.
3. A real-shape fixture reproduces the current miss.
4. The candidate preserves the exact model-visible token prefix and only
   changes checkpoint retention/reuse.
5. Three replay compactions show the expected reuse and unchanged output
   completion.

A server reload is justified only after the source and fixture gates pass. The
Python server load costs roughly two to three minutes, so the expected saved
seconds must be stated before switching it.

For adoption:

- at least three real-shape compaction replays;
- no HTTP or execution errors;
- compaction finishes normally;
- ordinary 1/2-request prompt reuse and latency do not regress by more than 1%;
- 4 requests do not regress by more than 1%;
- 6/8 requests remain error-free and healthy;
- the observed upper bound is at least 1% of root-task time or the item is
  reclassified as a p99-only change.

## Stop rules

Stop before implementation if:

- the first divergence is real changed client content rather than prompt
  layout;
- the only way to reuse the cache changes model-visible instructions or
  history;
- the compaction prompt is larger than the selected context and the fallback
  is already required;
- the expected absolute saving cannot cover one server reload plus cold-cache
  cost for the measured frequency.

Do not repeat the exact-pin test, change the 262144 context, or scan cache
sizes without a newly measured divergence.

## Outcome

The frozen trace was not Strata traffic: its Codex provider was configured for
the direct DeepSeek API. A privacy-safe live capture through the active B4
server then reproduced three real Codex compactions with `99.1002-99.1304%`
reuse and normal completion.

The current kept-tool cache therefore has no defect to implement against. The
implementation branch stops here without a server restart. The next main item
is `AGENT-TOOL-CONTEXT-DIET`, selected from the full local-session scan rather
than the misattributed compaction trace.

