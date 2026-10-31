# AGENT-STATIC-SYSTEM-PREFIX contract

Date: 2026-10-31

Status: preregistered before source changes.

## Problem

The Qwen chat template currently writes the per-conversation tool list before
the merged agent instructions. Codex main and child threads usually share the
large instruction block but can have different tool sets. A difference in the
first tool therefore ends the common prefix before the shared instructions.

Deterministic source measurement with one 54,015-character shared system block
and two otherwise identical single-tool requests:

| Item | Current |
|---|---:|
| Rendered characters | 73,343 |
| Common prefix with different tools | 325 |
| Shared fraction | 0.44% |

The four-thread real-server LRU test also observed zero cold reuse when only
the tool name differed. This is static prompt evidence, not yet an end-to-end
performance result.

## Candidate

Add an opt-in template value `system_before_tools`. When false, the template
must produce byte-identical output to the current path. When true and tools are
present, render within the same system turn:

1. reasoning-effort instructions;
2. merged system and developer instructions;
3. the existing tool boilerplate and serialized tool list;
4. the existing closing system marker.

The candidate changes only block order. The exact tool JSON, system text,
tool-call grammar, parser, sampling, engine and default configuration stay
unchanged.

Expose the opt-in through the server config key
`codex_system_before_tools: true`, applied only to Responses requests headed to
the engine. Thread-title shortcuts do not use it.

## Gates

Source and focused tests:

1. default-off rendering is byte-identical on a frozen fixture;
2. enabled rendering contains the same system text and tool JSON;
3. on the 73,343-character fixture, common prefix with different tools rises
   from 325 characters to at least 54,000 characters;
4. tool order stability, thinking effort, tool-call grammar, conversation
   caching and existing Responses tests still pass;
5. no extra tool is advertised and no requested tool is removed.

Real server:

1. record the current different-tool baseline before changing configuration;
2. B3 starts with 6 slots / 2 groups, health loaded and the same engine hash;
3. at least four main/child requests with the same long instruction block and
   distinct tools each return a valid call to their own declared tool;
4. after the first cold prompt, later sibling requests report a materially
   higher cached-token count;
5. title and compaction paths still pass, with zero foreign requests;
6. zero HTTP, execution, CUDA, timeout or finish anomalies;
7. a 1/2-stream end-to-end comparison shows at least 1% where the shared prefix
   is reusable, and four streams do not regress by more than 1%.

If tool choice or answer quality changes, disable the option and retain only
the accepted title and compaction improvements.

## Rollback

Set `codex_system_before_tools` to false or remove it and restart the Python
server. The template's default branch remains the current production path.

## Limits

This helps sibling conversations that share a stable instruction/system block
and start after the first prefix is cached. Four truly simultaneous cold
requests cannot share that first read. It does not claim to help unrelated
conversations or replace long-context compute.
