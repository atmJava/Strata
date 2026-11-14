"""serve/test_deployment_policy.py - the defaults this deployment promises, asserted where a reader will look.

AGENTS.md says the default path is the adapted path: a behaviour this box always wants is code, not a setting. That
promise is only kept if a change that turns one of them back into a switch fails a test, and it should fail one test,
not three files away. This is that one file.

    python -m unittest serve.test_deployment_policy -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import serve.server as server  # noqa: E402

HERE = Path(__file__).resolve().parent


def _serve_mentions():
    """name -> the serve/*.py files (tests excluded) that name it, built once."""
    global _SERVE_MENTIONS
    if _SERVE_MENTIONS is None:
        mentions = {}
        token = re.compile(r"\bSTRATA_[A-Z0-9_]+\b")
        for path in sorted(HERE.glob("*.py")):
            if path.name.startswith("test_"):
                continue
            for name in set(token.findall(path.read_text(encoding="utf-8"))):
                mentions.setdefault(name, []).append(path.name)
        _SERVE_MENTIONS = mentions
    return _SERVE_MENTIONS


def source_reads(name):
    """The files under serve/ that name this environment variable (tests excluded)."""
    return sorted(_serve_mentions().get(name, []))


def _engine_readers():
    """name -> the src/ and include/ C++ files whose getenv() reads it, built once."""
    global _ENGINE_READERS
    if _ENGINE_READERS is None:
        suffixes = {".c", ".cc", ".cpp", ".cu", ".cuh", ".h", ".hpp", ".inc", ".inl"}
        pattern = re.compile(r"\bgetenv\s*\(\s*\"(STRATA_[A-Z0-9_]+)\"")
        readers = {}
        for folder in (ROOT / "src", ROOT / "include"):
            for path in sorted(folder.rglob("*")):
                if path.suffix not in suffixes or not path.is_file():
                    continue
                for name in set(pattern.findall(path.read_text(encoding="utf-8"))):
                    readers.setdefault(name, []).append(str(path.relative_to(ROOT)))
        _ENGINE_READERS = readers
    return _ENGINE_READERS


def engine_reads(name):
    """C++ files that read this environment variable, comments and documents excluded."""
    return sorted(_engine_readers().get(name, []))


_SERVE_MENTIONS = None
_ENGINE_READERS = None

# Every environment switch the convergence rounds removed.  The engine must have no getenv() for any of them, and
# serve must not name one either: a performance decision is code, and this is the one place that says so for all of
# them.  server.py still passes STRATA_BATCH_TELEMETRY to the engine and names STRATA_VERIFY_TRACE in a comment, so
# those two are asserted engine-side only (the launcher hand-off is asserted separately).
REMOVED_SWITCHES = (
    "STRATA_ACT_DUMP", "STRATA_ACT_DUMP_COV", "STRATA_ACT_DUMP_LAYERS",
    "STRATA_ADAPT_ASYNC", "STRATA_ADAPT_ASYNC_BATCH", "STRATA_ADAPT_ASYNC_WAIT",
    "STRATA_ADAPT_CALLBACK_WARMUP", "STRATA_ADAPT_CHECKRES", "STRATA_ADAPT_EVICT_SYNC",
    "STRATA_ADAPT_JOB_CPU", "STRATA_ADAPT_LAG", "STRATA_ADAPT_NOWAIT",
    "STRATA_ADAPT_OVERLAP", "STRATA_ADAPT_OVERLAP_RESIDENT", "STRATA_ADAPT_OVERLAP_SLOW_US",
    "STRATA_ADAPT_PICK_INLINE", "STRATA_ADAPT_SIGNAL", "STRATA_ADMIT_MAX_WAIT_S",
    "STRATA_ADMIT_MERGE_FIRST", "STRATA_ADMIT_SHORT_FIRST", "STRATA_ARENA_LOCK",
    "STRATA_ARENA_MMAP", "STRATA_ARENA_PIN_GIB", "STRATA_ARENA_RELEASE",
    "STRATA_ARENA_THP", "STRATA_ARGMAX_ROWS_SM70", "STRATA_ATTN_DAILY",
    "STRATA_ATTN_LANECELL", "STRATA_ATTN_MERGE_V2", "STRATA_ATTN_PRE75",
    "STRATA_ATTN_V3", "STRATA_AUX_CPUS", "STRATA_AUX_STAGER",
    "STRATA_B6_MMVQ_ROWS", "STRATA_BATCH_ATTN_ROWS", "STRATA_BATCH_DECODE_SHARE",
    "STRATA_BATCH_FAIR_SERVE", "STRATA_BATCH_GDN_GROUPS", "STRATA_BATCH_GROUPS_NV_SERIAL",
    "STRATA_BATCH_MTP", "STRATA_BATCH_MTP_SERIAL", "STRATA_BATCH_SELF_COMMIT",
    "STRATA_BATCH_SERIAL_TWO", "STRATA_BATCH_SIDE_MIXER", "STRATA_BATCH_TELEMETRY",
    "STRATA_BATCH_TRACE", "STRATA_BATCH_YIELD_CHUNKS", "STRATA_BENCH_QMIXED",
    "STRATA_CACHE_MESSAGE_BOUNDARY", "STRATA_CKPT_POOL", "STRATA_CKPT_REREAD",
    "STRATA_CKPT_THREADS", "STRATA_COLD_OVERLAP", "STRATA_COLD_TRACE",
    "STRATA_COMMIT_BATCH", "STRATA_COMMIT_SYNC", "STRATA_COMMIT_TIMING",
    "STRATA_CPU_IQ_ALL_EXACT1", "STRATA_CPU_YMM", "STRATA_CVEC_FUSE",
    "STRATA_CVEC_FUSE_CHECK", "STRATA_DBG_CPU_GATE", "STRATA_DBG_FEAT",
    "STRATA_DBG_FEAT_LAYERS", "STRATA_DBG_FORCE_SLOW_LAYER", "STRATA_DBG_GDN",
    "STRATA_DBG_NAN", "STRATA_DBG_POOL_GUARD", "STRATA_DBG_ROUTE_OVERRIDE",
    "STRATA_DECODE_TIMING", "STRATA_DEC_BATCH", "STRATA_DEFER_TITLE_REQUESTS",
    "STRATA_DEQUANT_WIDE", "STRATA_DF2_FORK", "STRATA_DF2_PRIO",
    "STRATA_DF2_PROFILE", "STRATA_DF2_SKIP", "STRATA_DF2_TIMING",
    "STRATA_DF2_WAITCOMMIT", "STRATA_DF_BRANCH", "STRATA_DF_PLE",
    "STRATA_DF_QB_Q4", "STRATA_DIAG", "STRATA_DISJOINT_ADAPT",
    "STRATA_DMA_BATCH", "STRATA_DOORBELL_STORE", "STRATA_DRAFT_TIMING",
    "STRATA_DSH_PTC_TOOL_REWRITE", "STRATA_DUMMY_NODES", "STRATA_DUMP_FIRST_LOGITS",
    "STRATA_DUMP_MOE_INPUT", "STRATA_DUMP_MOE_LAYER", "STRATA_EARLY_REMOTE_CONTEXTS",
    "STRATA_EMB_REUSE_ACCOUNT", "STRATA_ENGINE_NICE", "STRATA_EXCHANGE_ROTATE",
    "STRATA_F16_RANGE", "STRATA_FETCH_THREADS", "STRATA_FILE_CACHE_OLD_RULE",
    "STRATA_FILE_RELEASE", "STRATA_FILE_TIER_PROBE", "STRATA_FILL_AHEAD",
    "STRATA_FLAG_FILE", "STRATA_FORCE_AVX2", "STRATA_FORCE_ISA",
    "STRATA_FS_AHEAD", "STRATA_FS_SLOTS", "STRATA_FUSED_SWIGLU_Q81",
    "STRATA_FUSE_HEAD_GR", "STRATA_FUSE_WAIT", "STRATA_GDN_CHUNKED",
    "STRATA_GDN_COMMIT_SPLIT", "STRATA_GDN_CONVL2", "STRATA_GDN_CONV_SERIAL",
    "STRATA_GDN_HEAD", "STRATA_GDN_KEYHEAD", "STRATA_GDN_PIPELINE",
    "STRATA_GDN_REC_HEADS", "STRATA_GDN_SPLIT", "STRATA_GFX12_CHUNK",
    "STRATA_GROUPED_PAIR_MIN_HITS", "STRATA_GROUP_COPY", "STRATA_GR_DOWN_MAX4",
    "STRATA_GR_DOWN_TG", "STRATA_GR_FAST", "STRATA_GR_NORM_FAST",
    "STRATA_GR_UNFUSED", "STRATA_GR_UP75", "STRATA_GR_V3",
    "STRATA_GU_SWIGLU", "STRATA_HANDOFF_P2P", "STRATA_HANDOFF_PIPE",
    "STRATA_HANDOFF_PIPE_WORKER", "STRATA_HCD_EXACT", "STRATA_HC_Q8",
    "STRATA_HC_Q8_INJECT", "STRATA_HC_REQ8", "STRATA_HEAD_MIX_MULTI",
    "STRATA_HEAD_SPLIT", "STRATA_HOST_CORE", "STRATA_HTTP_BACKLOG",
    "STRATA_IDX_FP16_CHECK", "STRATA_INDEXER_PER_TOKEN", "STRATA_IO_PF_AHEAD",
    "STRATA_IO_PF_BUDGET_FRAC", "STRATA_IO_PF_BUDGET_MB", "STRATA_IO_PF_GATE",
    "STRATA_IO_PF_GATE_HI", "STRATA_IO_PF_GATE_LO", "STRATA_IO_PF_GATE_USEFUL",
    "STRATA_IO_PF_MINCONF", "STRATA_IO_PF_SCHED", "STRATA_IO_PF_STAGE",
    "STRATA_IO_PF_TARGET", "STRATA_IO_PF_THREADS", "STRATA_IO_PREFETCH",
    "STRATA_IO_PREFETCH_DEPTH", "STRATA_IO_STATS", "STRATA_IO_THREADS",
    "STRATA_IQ256_GATHER", "STRATA_IQ_GATHER", "STRATA_IQ_MT_MIN",
    "STRATA_IQ_PREFETCH", "STRATA_IQ_STAGE_GRID", "STRATA_IQ_STAGE_GRID_MMVQ",
    "STRATA_IQ_STAGE_TABLES", "STRATA_KEEP_DSH_COMPACTION_TOOLS", "STRATA_KEEP_EMPTY_TURNS",
    "STRATA_KEEP_LEFTOVERS", "STRATA_KEEP_MAPPING", "STRATA_KV_GROW",
    "STRATA_KV_GROW_FLOOR", "STRATA_KV_GROW_HOLD", "STRATA_KV_GROW_INIT",
    "STRATA_KV_GROW_STEP", "STRATA_KV_HOST_DMA", "STRATA_KV_HOST_REGISTER",
    "STRATA_KV_MAIN_OFF", "STRATA_KV_PREFETCH", "STRATA_KV_RING_OFF",
    "STRATA_KV_ROT", "STRATA_KV_STAGE_OWN", "STRATA_LAYER_SPLIT_GATE",
    "STRATA_LAYER_SPLIT_PROBE", "STRATA_LFUSE", "STRATA_LFUSE_GATE",
    "STRATA_LFUSE_PAIR", "STRATA_LM_PREFETCH", "STRATA_LOGPOS",
    "STRATA_LOGPOS_EXTRA", "STRATA_LOGPOS_TOPK", "STRATA_LOOKAHEAD",
    "STRATA_LOOKAHEAD_K", "STRATA_LOOKAHEAD_STATS", "STRATA_LOOKUP_CHAIN_FIXED",
    "STRATA_MEMORY_LIMIT_MIB", "STRATA_MMQ_GROUP_SORT", "STRATA_MMQ_RESIDENT_SORT_NE",
    "STRATA_MMVF_ROWS", "STRATA_MMVQ_IL", "STRATA_MMVQ_IL_Q3K",
    "STRATA_MMVQ_IL_ROWS", "STRATA_MP_STAGED_READ", "STRATA_MP_STAGED_READ_MIN_TOKENS",
    "STRATA_MTP_BATCH", "STRATA_MTP_BATCH_F16", "STRATA_MTP_BATCH_RING",
    "STRATA_MTP_CATCHUP_ALL", "STRATA_MTP_FULL_HEAD", "STRATA_MTP_PREFILL_SYNC",
    "STRATA_MTP_PRIORITY", "STRATA_MTP_PROFILE", "STRATA_MTP_SHARED_BRANCH",
    "STRATA_MTP_STAGE0", "STRATA_MTP_TOP2", "STRATA_MULTI_BLOCK_ARGMAX",
    "STRATA_NAN_GUARD", "STRATA_NAN_INJECT", "STRATA_NO_ARENA_THP",
    "STRATA_NO_AVXVNNI", "STRATA_NO_BATCH_KV_STEP", "STRATA_NO_IQ128",
    "STRATA_NO_IQ256", "STRATA_NO_IQ4NL", "STRATA_NO_IQ512",
    "STRATA_NO_IQ512X1", "STRATA_NO_LARGEPAGES", "STRATA_NO_MMVQ_ROWS2",
    "STRATA_NO_MULTI_GR", "STRATA_NO_NORM_ROPE", "STRATA_NO_Q8K_AVX1",
    "STRATA_NO_Q8K_AVX2", "STRATA_NUMA_POOL", "STRATA_NUMA_SPLIT",
    "STRATA_OLD_GROUPED", "STRATA_ONE_TOKEN_COMMIT", "STRATA_OWNED_PRICE",
    "STRATA_PARALLEL_SOLO", "STRATA_PARITY_DUMP", "STRATA_PARTIAL_PIN",
    "STRATA_PARTIAL_PIN_GIB", "STRATA_PA_V1_PREFETCH", "STRATA_PA_V2T",
    "STRATA_PCIE_AUTO_SPLIT", "STRATA_PCIE_BALANCE", "STRATA_PCIE_FRAC_DEFAULT",
    "STRATA_PCIE_MIN1", "STRATA_PEER_DIRECT", "STRATA_PEER_HOT",
    "STRATA_PEER_HOT_AT", "STRATA_PEER_NO_P2P", "STRATA_PF_F16",
    "STRATA_PF_FUSED", "STRATA_PF_FUSED_NATIVE", "STRATA_PF_FUSED_SM75",
    "STRATA_PF_FUSED_TILE", "STRATA_PF_GEMM", "STRATA_PF_HCDOWN",
    "STRATA_PF_PAD", "STRATA_PF_PEER_COMPACT", "STRATA_PF_PEER_F16",
    "STRATA_PF_PEER_GATHER", "STRATA_PF_PEER_HOST", "STRATA_PF_PEER_OUT_PIPE",
    "STRATA_PF_PEER_RING", "STRATA_PF_PEER_STREAM", "STRATA_PF_PEER_SUMS",
    "STRATA_PF_PEER_XSTREAM", "STRATA_PF_STEP_SYNC", "STRATA_PF_SWITCH_MIN_T",
    "STRATA_PIN_BY_NEED", "STRATA_PIN_BY_NEED_SKIP", "STRATA_PIN_DSH_TOOL_PREFIX",
    "STRATA_PIN_GUARD", "STRATA_PIN_RESERVE_GIB", "STRATA_PIN_RESERVE_MIB",
    "STRATA_PIPED_CONCURRENT", "STRATA_PIPED_CONCURRENT_PRIO", "STRATA_PIPED_CONCURRENT_UNPIN",
    "STRATA_PIPED_DECODE_MAX_MS", "STRATA_PIPED_DECODE_MIN_MS", "STRATA_PIPED_MTP",
    "STRATA_PIPED_READ_PIECE", "STRATA_PIPELINE_ADAPT_ASYNC", "STRATA_PIPELINE_AGREE",
    "STRATA_PIPELINE_AUTO", "STRATA_PIPELINE_AUTO_MIN_STAGES", "STRATA_PIPELINE_DEBUG",
    "STRATA_PIPELINE_DOOM_SKIP", "STRATA_PIPELINE_FORCE_MISS", "STRATA_PIPELINE_LOG",
    "STRATA_PIPELINE_LOOKUP_PON", "STRATA_PIPELINE_PRESTAGE", "STRATA_PIPELINE_SNAP_OVERLAP",
    "STRATA_PIPELINE_SPEC_DEPTH", "STRATA_PIPELINE_SWITCH", "STRATA_PIPELINE_THETA",
    "STRATA_PIPELINE_TRACE", "STRATA_PIPE_BYIELD_NODRAIN", "STRATA_PLE_AUTO_MMAP",
    "STRATA_PLE_BATCH", "STRATA_PLE_CHECK", "STRATA_PLE_GGUF",
    "STRATA_PLE_PROBE", "STRATA_POOL_CORES", "STRATA_POOL_SPIN_US",
    "STRATA_POOL_TRACE", "STRATA_PREFILL_AUTO_MAX", "STRATA_PREFILL_BF16X2",
    "STRATA_PREFILL_DUMP_R", "STRATA_PREFILL_DUMP_R_ALL", "STRATA_PREFILL_EQUAL",
    "STRATA_PREFILL_FUSED_STAGED", "STRATA_PREFILL_GROUP_GATHER", "STRATA_PREFILL_HELP",
    "STRATA_PREFILL_HELPER_SOURCE", "STRATA_PREFILL_HELP_FRAC", "STRATA_PREFILL_HOST_PLAN",
    "STRATA_PREFILL_ISSUER", "STRATA_PREFILL_LAYER_MAJOR", "STRATA_PREFILL_LEND_PCT",
    "STRATA_PREFILL_LM_EVEN", "STRATA_PREFILL_LM_MARGIN_MIB", "STRATA_PREFILL_LM_R",
    "STRATA_PREFILL_LM_TOKENS", "STRATA_PREFILL_MMQ", "STRATA_PREFILL_PIPE",
    "STRATA_PREFILL_PLAN_TOKS", "STRATA_PREFILL_RING", "STRATA_PREFILL_STAGED_AHEAD",
    "STRATA_PREFILL_STREAM_AHEAD", "STRATA_PREFILL_STREAM_MIN", "STRATA_PREFILL_TIMING",
    "STRATA_PRIMARY_DEVICE", "STRATA_PROMPT_ATTN_OLD", "STRATA_PROMPT_ATTN_Q4",
    "STRATA_PROMPT_ATTN_Q4_TC", "STRATA_PROMPT_ATTN_V1", "STRATA_PROMPT_DIRECT",
    "STRATA_Q2_BITPLANE", "STRATA_Q2_WARP", "STRATA_Q6P_GRID",
    "STRATA_Q6P_SELFTEST", "STRATA_Q6_PACKED", "STRATA_Q8_PACKED",
    "STRATA_QFUSE", "STRATA_QFUSE_HC_LOCAL", "STRATA_QIL",
    "STRATA_QSA_DUMP", "STRATA_QSA_DUMP_LAST", "STRATA_QSA_DUMP_LAYER",
    "STRATA_QSA_DUMP_STEPS", "STRATA_QSA_EARLY_EXIT", "STRATA_QSA_ROWS",
    "STRATA_QUANT_GATHER", "STRATA_RAM_ADAPT", "STRATA_RAM_ADAPT2",
    "STRATA_RAM_ADAPT_CACHED_W", "STRATA_RAM_ADAPT_PER_LAYER", "STRATA_RAM_ADAPT_PROMPT_W",
    "STRATA_RAM_ADAPT_QUEUE", "STRATA_RAM_ADAPT_SKIP_TOP", "STRATA_READ_AHEAD",
    "STRATA_REFILL_BLOCKING", "STRATA_REFILL_PARTS", "STRATA_REFILL_SERIAL",
    "STRATA_REMOTE_SPIN", "STRATA_REMOTE_ZEROCOPY", "STRATA_REPLICA_START",
    "STRATA_REQUEST_LINES", "STRATA_RESIDENT_HEADROOM_GIB", "STRATA_RESIDENT_PIN",
    "STRATA_RING_BYTES", "STRATA_ROPE_TABLE", "STRATA_ROUTE_PER_TOKEN",
    "STRATA_ROUTE_PRIOR", "STRATA_ROUTE_PROJ_PER_TOKEN", "STRATA_ROUTE_RESIDENT",
    "STRATA_ROUTE_RESIDENT_MTP", "STRATA_ROUTE_RESIDENT_RANKS", "STRATA_ROUTE_STATS",
    "STRATA_RSS_TRACE", "STRATA_RUNTIME_PROFILE", "STRATA_S2_SWIGLU_Q8",
    "STRATA_SCORES_HALVE", "STRATA_SCORES_MULTI", "STRATA_SCORES_THREAD",
    "STRATA_SELECT_OLD", "STRATA_SEL_OVERLAP", "STRATA_SESSION_BUFFERED",
    "STRATA_SESSION_GOLDEN_PRINT", "STRATA_SH_FORK_LATE", "STRATA_SH_STREAM",
    "STRATA_SIDE_MIXER", "STRATA_SLOT_CKPT_MOVE", "STRATA_SLOT_COPY_BATCHSYNC",
    "STRATA_SLOT_COPY_DIRECT", "STRATA_SM75_IL", "STRATA_SM75_PERF",
    "STRATA_SNAPSHOT_FULL_CAPTURE", "STRATA_SNAPSHOT_VERIFY", "STRATA_SPEC_ADAPT_EVERY",
    "STRATA_SPEC_ADAPT_WARM", "STRATA_SPEC_COST", "STRATA_SPEC_COST_TABLE",
    "STRATA_SPEC_COUPLED", "STRATA_SPEC_DEPTH", "STRATA_SPEC_MIN_TEMP",
    "STRATA_SPEC_PROB", "STRATA_SPEC_PROB_DT", "STRATA_SPEC_PROB_GATE",
    "STRATA_SPEC_TRACE", "STRATA_SPEC_T_CYCLE", "STRATA_SPEC_WATCH",
    "STRATA_SPLIT_CHUNK16K", "STRATA_SPLIT_COVER_B", "STRATA_SPLIT_LINK_ABS",
    "STRATA_SPLIT_MISS_MS", "STRATA_SPLIT_MTP_BATCH", "STRATA_SPLIT_NEAR_TIE_PCT",
    "STRATA_SPLIT_OVERLAP", "STRATA_SPLIT_OWN", "STRATA_SPLIT_OWN_BUFFERS",
    "STRATA_SPLIT_OWN_PLACE", "STRATA_SPLIT_RING", "STRATA_SPLIT_SMALL_MAX",
    "STRATA_SPLIT_SMALL_OWN", "STRATA_SPLIT_SPEED_SCALE", "STRATA_SPLIT_TIMING",
    "STRATA_SPLIT_UNIFORM", "STRATA_SPLIT_WAIT_MS", "STRATA_SSD_GBPS",
    "STRATA_SSD_KEEPALIVE", "STRATA_SSD_KEEPALIVE_WINDOW", "STRATA_STAGER_BATCH",
    "STRATA_STAGER_RING", "STRATA_STAGER_SLEEP", "STRATA_STAGER_SSD",
    "STRATA_STAGER_THREADS", "STRATA_STAGE_PIN", "STRATA_STAGE_TRIM",
    "STRATA_START_RETRIES", "STRATA_STATE_HASH", "STRATA_STATE_HASH_GDN",
    "STRATA_STREAM_WRITE_TIMEOUT_S", "STRATA_TAP_FILE", "STRATA_TAP_LAYERS",
    "STRATA_TC_SM75", "STRATA_TG_FLUSH_US", "STRATA_TOPK_ACTIVE_ANY",
    "STRATA_TOPK_CAPACITY_GUARD", "STRATA_TOPK_OLD", "STRATA_TOPK_SAMPLE",
    "STRATA_TOPK_SPLIT", "STRATA_TOPK_STREAM", "STRATA_TRACE",
    "STRATA_TRACE_ADAPT", "STRATA_TSUM", "STRATA_UMA_HEADROOM_GIB",
    "STRATA_UMA_RESERVE_GIB", "STRATA_UNBUFFERED_LOAD", "STRATA_VERIFY_ALL_RESIDENT",
    "STRATA_VERIFY_ARENA", "STRATA_VERIFY_ASYNC_LAUNCH", "STRATA_VERIFY_COMPLEMENT",
    "STRATA_VERIFY_DEBUG", "STRATA_VERIFY_DEVICE_PLAN", "STRATA_VERIFY_EARLY_PLE_FLAG",
    "STRATA_VERIFY_NODES", "STRATA_VERIFY_PROFILE", "STRATA_VERIFY_QDEDUP",
    "STRATA_VERIFY_TRACE", "STRATA_WATCHDOG_IO_S", "STRATA_WATCHDOG_S",
    "STRATA_XN_F16",
)


class Admission(unittest.TestCase):
    def test_short_first_is_a_constant(self):
        self.assertEqual(server.ADMIT_SHORT_FIRST, 2048)

    def test_no_switch_can_change_it(self):
        self.assertEqual(source_reads("STRATA_ADMIT_SHORT_FIRST"), [])

    def test_the_long_wait_fallback_has_no_switch(self):
        self.assertEqual(server.ADMIT_MAX_WAIT_S, 30.0)
        self.assertEqual(source_reads("STRATA_ADMIT_MAX_WAIT_S"), [])

    def test_staged_read_is_a_constant(self):
        self.assertEqual(server.STAGED_READ_MIN_TOKENS, 2048)

    def test_no_switch_can_change_staged_read(self):
        for name in ("STRATA_MP_STAGED_READ", "STRATA_MP_STAGED_READ_MIN_TOKENS"):
            with self.subTest(name=name):
                self.assertEqual(source_reads(name), [])

    def test_serial_two_is_a_constant(self):
        self.assertIs(server.SERIAL_TWO, True)

    def test_no_switch_can_change_serial_two(self):
        self.assertEqual(source_reads("STRATA_BATCH_SERIAL_TWO"), [])

    def test_going_back_to_the_solo_path_has_no_switch(self):
        self.assertEqual(source_reads("STRATA_PARALLEL_SOLO"), [])


class TheFourDshBehaviours(unittest.TestCase):
    """Each is a default with a shape check of its own, never an opt-in."""

    def setUp(self):
        from serve.test_server import ByteTokenizer, ChatTemplate, MockEngine
        tok = ByteTokenizer()
        self.svc = server.Service(MockEngine(tok, "ok", max_context=1 << 20), tok,
                                  ChatTemplate(ROOT / "serve" / "chat_template.jinja"))

    def test_they_are_on(self):
        self.assertIs(self.svc.defer_title_requests, True)
        self.assertIs(self.svc.pin_dsh_tool_prefix, True)
        self.assertIs(self.svc.ptc_tool_rewrite, True)
        self.assertIs(self.svc.keep_dsh_compaction_tools, True)

    def test_no_switch_can_change_them(self):
        for name in ("STRATA_DEFER_TITLE_REQUESTS", "STRATA_PIN_DSH_TOOL_PREFIX",
                     "STRATA_DSH_PTC_TOOL_REWRITE", "STRATA_KEEP_DSH_COMPACTION_TOOLS"):
            with self.subTest(name=name):
                self.assertEqual(source_reads(name), [])


class EngineDefaults(unittest.TestCase):
    """Behaviour this deployment uses unconditionally must not return as an environment switch."""

    def test_folded_switches_have_no_environment_reader(self):
        for name in (
            "STRATA_SLOT_COPY_DIRECT",
            "STRATA_SLOT_COPY_BATCHSYNC",
            "STRATA_CKPT_POOL",
            "STRATA_CKPT_THREADS",
            "STRATA_ADMIT_MERGE_FIRST",
            "STRATA_PIPE_BYIELD_NODRAIN",
            "STRATA_BATCH_SELF_COMMIT",
            "STRATA_STAGE_TRIM",
            "STRATA_PIPED_CONCURRENT",
            "STRATA_PIPED_CONCURRENT_PRIO",
            "STRATA_MTP_STAGE0",
            "STRATA_PIPELINE_AUTO",
            "STRATA_PIPELINE_AUTO_MIN_STAGES",
            "STRATA_PCIE_AUTO_SPLIT",
            "STRATA_GFX12_CHUNK",
            "STRATA_SPLIT_CHUNK16K",
            "STRATA_UMA_HEADROOM_GIB",
            "STRATA_UMA_RESERVE_GIB",
            "STRATA_ATTN_PRE75",
            # round 4: admission / reuse tuning knobs folded into the defaults
            "STRATA_ADAPT_ASYNC",
            "STRATA_ADAPT_CALLBACK_WARMUP",
            "STRATA_ADAPT_CHECKRES",
            "STRATA_ADAPT_EVICT_SYNC",
            "STRATA_ADAPT_JOB_CPU",
            "STRATA_ADAPT_LAG",
            "STRATA_ADAPT_NOWAIT",
            "STRATA_ADAPT_OVERLAP",
            "STRATA_ADAPT_OVERLAP_RESIDENT",
            "STRATA_ADAPT_OVERLAP_SLOW_US",
            "STRATA_ADAPT_SIGNAL",
            "STRATA_PIPELINE_ADAPT_ASYNC",
            "STRATA_DISJOINT_ADAPT",
            "STRATA_FILL_AHEAD",
            "STRATA_FS_AHEAD",
            "STRATA_FS_SLOTS",
            "STRATA_LOOKAHEAD",
            "STRATA_LOOKAHEAD_K",
            "STRATA_LOOKAHEAD_STATS",
            "STRATA_IO_PF_AHEAD",
            "STRATA_IO_PF_BUDGET_FRAC",
            "STRATA_IO_PF_BUDGET_MB",
            "STRATA_IO_PF_GATE",
            "STRATA_IO_PF_GATE_HI",
            "STRATA_IO_PF_GATE_LO",
            "STRATA_IO_PF_GATE_USEFUL",
            "STRATA_IO_PF_MINCONF",
            "STRATA_IO_PF_SCHED",
            "STRATA_IO_PF_STAGE",
            "STRATA_IO_PF_TARGET",
            "STRATA_IO_PF_THREADS",
            "STRATA_IO_PREFETCH",
            "STRATA_IO_PREFETCH_DEPTH",
            "STRATA_IO_STATS",
            "STRATA_IO_THREADS",
            "STRATA_PIN_BY_NEED",
            "STRATA_PIN_BY_NEED_SKIP",
            "STRATA_RAM_ADAPT",
            "STRATA_RAM_ADAPT2",
            "STRATA_RAM_ADAPT_CACHED_W",
            "STRATA_RAM_ADAPT_PER_LAYER",
            "STRATA_RAM_ADAPT_PROMPT_W",
            "STRATA_RAM_ADAPT_QUEUE",
            "STRATA_RAM_ADAPT_SKIP_TOP",
        ):
            with self.subTest(name=name):
                self.assertEqual(engine_reads(name), [])


class ServerKnobs(unittest.TestCase):
    """The serve/ side of the same rule: the behaviour this box runs is code, not an environment read."""

    def test_the_folded_knobs_have_no_environment_reader(self):
        for name in (
            "STRATA_STREAM_WRITE_TIMEOUT_S",
            "STRATA_START_RETRIES",
            "STRATA_HTTP_BACKLOG",
            "STRATA_REPLICA_START",
            "STRATA_KEEP_LEFTOVERS",
            "STRATA_BATCH_MTP",
            "STRATA_BATCH_TRACE",
            "STRATA_REQUEST_LINES",
            "STRATA_KEEP_EMPTY_TURNS",
            "STRATA_RUNTIME_PROFILE",
            "STRATA_ENGINE_NICE",
        ):
            with self.subTest(name=name):
                self.assertEqual(source_reads(name), [])

    def test_the_folded_knobs_are_their_defaults(self):
        self.assertEqual(server.STREAM_WRITE_TIMEOUT_S, 60.0)
        self.assertEqual(server.START_RETRIES, 3)
        self.assertEqual(server.Server.request_queue_size, 256)


class RemovedSwitches(unittest.TestCase):
    """Every switch the convergence rounds deleted stays deleted."""

    def test_the_engine_has_no_reader_for_any_removed_switch(self):
        for name in REMOVED_SWITCHES:
            with self.subTest(name=name):
                self.assertEqual(engine_reads(name), [])

    def test_serve_has_no_reader_for_any_removed_switch(self):
        for name in REMOVED_SWITCHES:
            if name in ("STRATA_BATCH_TELEMETRY", "STRATA_VERIFY_TRACE"):
                continue  # still named in serve/server.py: the launcher's hand-off and a comment
            with self.subTest(name=name):
                self.assertEqual(source_reads(name), [])

    def test_the_launcher_still_hands_batch_telemetry_to_the_engine(self):
        # The engine no longer reads it; server.py still sets it when the run config asks for it.  The assertion
        # records the one remaining mention rather than letting the name come back as a reader unnoticed.
        self.assertEqual(source_reads("STRATA_BATCH_TELEMETRY"), ["server.py"])


class Capacity(unittest.TestCase):
    def test_the_compaction_point_is_eighty_percent_of_200k(self):
        self.assertEqual(server.COMPACTION_TOKENS, 163_840)

    def test_the_run_config_carries_no_engine_switch(self):
        sys.path.insert(0, str(ROOT / "strata_control"))
        import sm75_profile
        self.assertEqual(sm75_profile.expected_env(), {})


if __name__ == "__main__":
    unittest.main()
