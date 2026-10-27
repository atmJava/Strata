# Strata server guides

This documentation belongs to the fixed Linux CUDA `sm_75` server distribution for
2 x RTX 2080 Ti and Qwen3.8-Flash-Next.

| Task | Guide |
|---|---|
| Build and place the engine | [Install and build](INSTALL.md) |
| Start serving from an existing config | [Terminal quick start](TERMINAL_QUICKSTART.md) |
| Understand every retained command | [Terminal reference](TERMINAL.md) |
| Review launch and profile details | [Terminal launch](TERMINAL_LAUNCH.md) |
| Check the production shape | [Models and production profile](MODELS.md) |
| Understand the owner-local runtime protocol | [Management protocol](MANAGEMENT_PROTOCOL.md) |
| Give setup instructions to another agent | [AI setup](AI_SETUP.md) |
| Verify facts and limitations | [AI reference](AI_REFERENCE.md) |

Current production state, measured results, risks and recovery steps are in:

- [STATE.md](STATE.md)
- [HANDOVER.md](HANDOVER.md)
- [ITERATION_LOG.md](ITERATION_LOG.md)
- [EVIDENCE_LEDGER.md](EVIDENCE_LEDGER.md)

Older platform, packaging and model pages may remain as dated evidence, but they are not supported entry points and
do not describe this distribution.
