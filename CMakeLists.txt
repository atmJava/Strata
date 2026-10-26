# Strata server build.
#
# This tree is the CLI + server distribution for the production 2x RTX 2080 Ti host.
# It builds one CUDA target for Turing sm_75. Cross-vendor, macOS, Windows, Pascal,
# Volta, Ampere, Ada and Blackwell build paths are not part of this distribution.
cmake_minimum_required(VERSION 3.24)
project(strata VERSION 0.1.51 LANGUAGES CXX)

if(WIN32 OR APPLE)
  message(FATAL_ERROR "This Strata distribution supports Linux only; the server target is Linux x86_64 + CUDA sm_75")
endif()

foreach(_removed_option STRATA_ENABLE_SYCL STRATA_ENABLE_HIP STRATA_HIP_GFX906
        STRATA_EXPERIMENTAL_SM60)
  if(DEFINED ${_removed_option} AND ${_removed_option})
    message(FATAL_ERROR "${_removed_option} is not part of the sm_75 server build")
  endif()
endforeach()
set(STRATA_ENABLE_CUDA ON)

add_compile_definitions(STRATA_VERSION="${PROJECT_VERSION}")

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

# The test suite and the micro benchmarks live in tests/ and bench/micro/, which the published source leaves out.
set(_strata_tests_default OFF)
if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/tests/CMakeLists.txt AND EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/bench/micro)
  set(_strata_tests_default ON)
endif()
option(STRATA_BUILD_TESTS "Build the test suite" ${_strata_tests_default})
option(STRATA_BUILD_CONVERSATION_TESTS "Build standalone conversation cache tests" OFF)
option(STRATA_WERROR "Treat compiler warnings as errors" OFF)
# #2027: defined here, before the GPU section reads it to register the native-expert tests (it was defined at the
# native-experts section far below, so a first configure registered four tests fewer than the second)
option(STRATA_NATIVE_EXPERTS "Native (IQ) expert support through ggml-cpu" ON)

if(STRATA_BUILD_CONVERSATION_TESTS)
  enable_testing()
  add_executable(conversation_cache_test src/core/conversation_cache_test.cpp)
  target_include_directories(conversation_cache_test PRIVATE include)
  add_test(NAME conversation_cache_test COMMAND conversation_cache_test)
  add_executable(conversation_split_failure_test src/core/conversation_split_failure_test.cpp)
  target_include_directories(conversation_split_failure_test PRIVATE include)
  add_test(NAME conversation_split_failure_test COMMAND conversation_split_failure_test)
  add_executable(conversation_memory_test src/core/conversation_memory_test.cpp src/core/conversation_memory.cpp)
  target_include_directories(conversation_memory_test PRIVATE include)
  add_test(NAME conversation_memory_test COMMAND conversation_memory_test)
  add_executable(conversation_file_test src/core/conversation_file_test.cpp src/core/conversation_file.cpp)
  target_include_directories(conversation_file_test PRIVATE include)
  add_test(NAME conversation_file_test COMMAND conversation_file_test)
  add_executable(session_save_reclaim_test src/core/session_save_reclaim_test.cpp src/core/conversation_file.cpp)
  target_include_directories(session_save_reclaim_test PRIVATE include)
  add_test(NAME session_save_reclaim_test COMMAND session_save_reclaim_test)
endif()

if(DEFINED CMAKE_CUDA_ARCHITECTURES AND NOT "${CMAKE_CUDA_ARCHITECTURES}" STREQUAL "75")
  message(FATAL_ERROR "This Strata server build supports CUDA sm_75 only; CMAKE_CUDA_ARCHITECTURES is '${CMAKE_CUDA_ARCHITECTURES}'")
endif()
set(CMAKE_CUDA_ARCHITECTURES 75 CACHE STRING "The only CUDA architecture supported by this server build" FORCE)
enable_language(CUDA)
find_package(CUDAToolkit REQUIRED)
message(STATUS "Strata server: Linux CUDA sm_75")
set(_strata_gpu_runtime_target CUDA::cudart)
set(_strata_gpu_blas_target CUDA::cublas)
set(_strata_gpu_include_directories ${CMAKE_CUDA_TOOLKIT_INCLUDE_DIRECTORIES})

# Warnings live on an INTERFACE target the project's own code links, NOT in add_compile_options: applied
# globally they would also hit FetchContent'd Catch2, whose -W4 warnings are not this project's to fix and
# whose /WX failures would be indistinguishable from our own.
#
# Every flag is guarded by COMPILE_LANGUAGE:CXX.  Without that guard nvcc receives bare `/W4 /utf-8` and dies
# with "A single input file is required for a non-link phase" - it parses them as inputs rather than host
# flags.  Host flags for CUDA must go through -Xcompiler, which is what COMPILE_LANGUAGE:CUDA does below.
add_library(strata_warnings INTERFACE)
target_compile_options(strata_warnings INTERFACE
  $<$<COMPILE_LANGUAGE:CXX>:-Wall>
  $<$<COMPILE_LANGUAGE:CXX>:-Wextra>)
if(STRATA_WERROR)
  target_compile_options(strata_warnings INTERFACE
    $<$<COMPILE_LANGUAGE:CXX>:-Werror>)
endif()

# ---- strata_artifact -------------------------------------------------------
# Header-only, and that is a deliberate property rather than an unfinished split: the GGUF reader and the
# scalar dequantizers hold no state worth hiding, and a header-only boundary cannot introduce a
# duplicate-symbol or missing-declaration bug into code that is already validated against the real artifact.
add_library(strata_artifact INTERFACE)
target_include_directories(strata_artifact INTERFACE ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(strata_artifact INTERFACE strata_warnings)

add_executable(strata-gguf src/artifact/gguf_reader.cpp)
target_link_libraries(strata-gguf PRIVATE strata_artifact)

add_executable(strata-dequant src/artifact/dequant.cpp)
target_link_libraries(strata-dequant PRIVATE strata_artifact)
# this file is now nothing but the CLI entry point, and its inherited guard spells that as a "selftest"
target_compile_definitions(strata-dequant PRIVATE STRATA_DEQUANT_SELFTEST)
if(STRATA_BUILD_TESTS)
  add_executable(batch_telemetry_test src/core/batch_telemetry_test.cpp)
  target_include_directories(batch_telemetry_test PRIVATE include)
  add_test(NAME batch_telemetry_test COMMAND batch_telemetry_test)
  add_executable(logit_bias_test src/core/logit_bias_test.cpp)
  target_include_directories(logit_bias_test PRIVATE include)
  add_test(NAME logit_bias_test COMMAND logit_bias_test)
  # the reader refuses a duplicate tensor name at open, naming it (a synthetic GGUF: no model, no GPU)
  add_executable(gguf_reader_test src/artifact/gguf_reader_test.cpp)
  target_link_libraries(gguf_reader_test PRIVATE strata_artifact)
  add_test(NAME gguf_reader_test COMMAND gguf_reader_test)
  # a split model's shards and their joint view: missing shard, split keys, duplicates, truncation (synthetic)
  add_executable(gguf_split_test tests/core/gguf_split_test.cpp)
  target_link_libraries(gguf_split_test PRIVATE strata_artifact)
  add_test(NAME gguf_split_test COMMAND gguf_split_test)
  # Qwen3.8-Flash-Next (qwen4exp): the architecture guard, geometry and layout tables (synthetic)
  add_executable(model_family_test tests/core/model_family_test.cpp)
  target_link_libraries(model_family_test PRIVATE strata_artifact)
  add_test(NAME model_family_test COMMAND model_family_test)
  # how --batch cuts a prompt read into pieces (a layer split with no slot decoding must still reach a BYIELD)
  add_executable(batch_read_test tests/program/batch_read_test.cpp)
  target_include_directories(batch_read_test PRIVATE include)
  add_test(NAME batch_read_test COMMAND batch_read_test)
  # the model's geometry from its metadata, and the check against the kernels' compiled shape (synthetic)
  add_executable(gguf_geometry_test tests/core/gguf_geometry_test.cpp)
  target_link_libraries(gguf_geometry_test PRIVATE strata_artifact)
  add_test(NAME gguf_geometry_test COMMAND gguf_geometry_test)

  # the auto layer split's minimum stage size (#1616): header-only rule, no GPU
  add_executable(split_rules_test tests/program/split_rules_test.cpp)
  target_include_directories(split_rules_test PRIVATE include)
  add_test(NAME split_rules_test COMMAND split_rules_test)

  # the prompt planner's host-memory model (#2824): header-only, no GPU
  add_executable(prefill_plan_test tests/program/prefill_plan_test.cpp)
  target_include_directories(prefill_plan_test PRIVATE include)
  add_test(NAME prefill_plan_test COMMAND prefill_plan_test)

  # the default CPU prefill share is not armed when every expert is resident (#1595): header-only rule, no GPU
  add_executable(share_rules_test tests/prefill/share_rules_test.cpp)
  target_include_directories(share_rules_test PRIVATE include)
  add_test(NAME share_rules_test COMMAND share_rules_test)

endif()

# ---- strata_plan ----------------------------------------------------------
# Also header-only.  A "planner" that hides state would be one whose decision is not inspectable, and the
# whole point of P1.S9 is that the decision is arithmetic you can check by hand.
add_library(strata_plan INTERFACE)
target_include_directories(strata_plan INTERFACE ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(strata_plan INTERFACE strata_warnings)

add_executable(strata-plan src/plan/plan_main.cpp)
target_link_libraries(strata-plan PRIVATE strata_plan)

# ---- strata_core (GPU runtime) ---------------------------------------------
# Only built when a GPU backend is on, so the Phase 1 targets keep working without a toolkit.
if(STRATA_ENABLE_CUDA)
  add_library(strata_core STATIC src/core/device.cu src/core/pinned.cu src/platform/memory.cpp
                               src/platform/gpu_event_pairs.cpp src/core/graph.cpp src/core/weights.cpp
                               src/core/layout.cpp)
  target_include_directories(strata_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
  # DFlash2 remains part of this CUDA server build.
  target_compile_definitions(strata_core PUBLIC STRATA_HAVE_DFLASH2=1)
  # The selected runtime is PUBLIC because graph.cpp is a HOST translation unit that calls cudaGraphLaunch and
  # cudaEventQuery: the .cu sources get cudart through the CUDA language rules, a .cpp does not, and the
  # linker error ("unresolved external symbol cudaGraphInstantiate") names the function rather than the cause.
  target_link_libraries(strata_core PUBLIC strata_plan strata_warnings ${_strata_gpu_runtime_target})

  add_executable(strata-device src/core/device_main.cpp)
  target_link_libraries(strata-device PRIVATE strata_core)

  add_executable(strata-load src/core/load_main.cpp)
  target_link_libraries(strata-load PRIVATE strata_core strata_kernels)

  add_executable(strata-concurrent src/core/concurrent_main.cpp)
  target_include_directories(strata-concurrent PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(strata-concurrent PRIVATE strata_core strata_kernels ${_strata_gpu_runtime_target})

  add_executable(strata-overlap src/core/overlap_main.cpp)
  # a .cpp that calls cudaMemcpyAsync itself needs the toolkit headers and cudart explicitly
  target_include_directories(strata-overlap PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(strata-overlap PRIVATE strata_core strata_kernels ${_strata_gpu_runtime_target})

  # The load test needs the 34 GB artifact, so it is registered ONLY where that artifact exists.  A test that
  # silently skips is a test that stops running; an absent registration is visible in the configure output.
  if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/pack/full/experts.bin)
    add_test(NAME expert_arena_load
             COMMAND strata-load --file ${CMAKE_CURRENT_SOURCE_DIR}/pack/full/experts.bin --layers 1)
    set_tests_properties(expert_arena_load PROPERTIES PASS_REGULAR_EXPRESSION "per-layer checksums")
  else()
    message(STATUS "strata: pack/full/experts.bin absent - the expert arena load test is NOT registered")
  endif()

  # ---- strata_kernels (P2.S2) ----
  # Naive first, each with a parity test against a reference that was itself validated against ggml.  The
  # kernels link `strata_artifact` for the scalar dequantizers, because using the VALIDATED scalar path as the
  # reference is the whole point - a kernel checked against another copy of itself proves nothing.
  add_library(strata_kernels STATIC src/kernels/cuda/dequant_s2.cu src/kernels/cuda/s2_gemv.cu
                          src/kernels/cuda/s_gemv.cu
                          src/kernels/cuda/s_gemv_pair.cu
                          src/kernels/cuda/s2_gemv_quads.cu
                          src/kernels/cuda/s2_gemv_fast.cu
                          src/kernels/cuda/router_top10.cu
                          src/kernels/cuda/quantize_act.cu
                          src/kernels/cuda/rope.cu
                          src/kernels/cuda/sampler.cu
                          src/kernels/cuda/s2_gemv_q8.cu
    src/kernels/cuda/s2_expert_grouped.cu
                          src/kernels/cuda/shared_expert.cu
                          src/kernels/cuda/gr.cu
                          src/kernels/cuda/gdn.cu
                          src/kernels/cuda/qsa.cu
                          src/kernels/cuda/elementwise.cu
                          src/kernels/cuda/bf16_gemv.cu
                          src/kernels/cuda/native_mmvq.cu
                          src/kernels/cuda/native_bf16.cu
                          src/kernels/cuda/native_gr_norm.cu
                          src/kernels/cuda/native_moe.cu
                          src/kernels/cuda/native_gdn.cu
                          src/kernels/cuda/native_gr_postops.cu
                          src/kernels/cuda/native_router.cu
                          src/kernels/cuda/route_prior.cu
                          src/kernels/cuda/native_gdn_preprocess.cu
                          src/kernels/cuda/native_qsa.cu
                          src/kernels/cuda/native_rope.cu
                          src/kernels/cuda/native_ple_postops.cu
                          src/kernels/cuda/native_flash_attn.cu
                          src/kernels/cuda/native_qsa_indexer.cu
                          src/kernels/cuda/ple.cu
                          src/kernels/cuda/kv_q8.cu
                          src/kernels/cuda/kv_stream.cu
                          src/kernels/cuda/kv_q4.cu
                          src/kernels/cuda/fused_gr.cu
                          src/kernels/cuda/cvec.cu
                          src/kernels/cuda/qsa_decode_attn.cu
                          src/kernels/cuda/qsa_prompt_attn.cu
                          src/kernels/cuda/fused_gdn.cu
                          src/kernels/cuda/dequant_bf16.cu
                          src/kernels/cuda/qsa_select.cu
                          src/kernels/cuda/verify_kernels.cu
                          src/kernels/cuda/dflash2.cu
                          src/kernels/cuda/iq_kernels.cu
                          src/kernels/ngram.cpp
                          src/ngram/ple_reader.cpp
                          src/platform/direct_file.cpp)
  target_link_libraries(strata_kernels PUBLIC strata_core strata_artifact)
  # perf-review D-1: the tensor-core prompt attention vs the FP32 kernel and FP64 (GPU, synthetic, no model); with
  # the tests, or alone with -DSTRATA_PARITY_PROMPT_ATTN=ON.
  if((STRATA_BUILD_TESTS OR STRATA_PARITY_PROMPT_ATTN) AND STRATA_ENABLE_CUDA)
    add_executable(qsa_prompt_attn_parity src/kernels/qsa_prompt_attn_parity.cpp)
    target_include_directories(qsa_prompt_attn_parity PRIVATE ${CMAKE_CUDA_TOOLKIT_INCLUDE_DIRECTORIES})
    target_link_libraries(qsa_prompt_attn_parity PRIVATE strata_kernels CUDA::cudart)
  endif()
  # #512: the top-k's active-block bound (Turing's dispatch, forced on any card by STRATA_TOPK_ACTIVE_ANY) against
  # the capacity rule, selected ids bitwise
  if(STRATA_BUILD_TESTS AND STRATA_ENABLE_CUDA)
    add_executable(qsa_topk_active_parity src/kernels/qsa_topk_active_parity.cpp)
    target_include_directories(qsa_topk_active_parity PRIVATE ${CMAKE_CUDA_TOOLKIT_INCLUDE_DIRECTORIES})
    target_link_libraries(qsa_topk_active_parity PRIVATE strata_kernels CUDA::cudart)
    add_test(NAME qsa_topk_active_parity COMMAND qsa_topk_active_parity)
    set_tests_properties(qsa_topk_active_parity PROPERTIES SKIP_RETURN_CODE 77)
    # STRATA_QSA_SLOTS: a batch window's scores, top-k and attention as one launch over rows of different slots
    # against one call per row, bitwise (synthetic, no model)
    add_executable(qsa_slots_parity src/kernels/qsa_slots_parity.cpp)
    target_include_directories(qsa_slots_parity PRIVATE ${CMAKE_CUDA_TOOLKIT_INCLUDE_DIRECTORIES})
    target_link_libraries(qsa_slots_parity PRIVATE strata_kernels CUDA::cudart)
    add_test(NAME qsa_slots_parity COMMAND qsa_slots_parity)
    add_test(NAME qsa_slots_parity_lanecell COMMAND qsa_slots_parity)
    set_tests_properties(qsa_slots_parity qsa_slots_parity_lanecell PROPERTIES SKIP_RETURN_CODE 77)
    set_tests_properties(qsa_slots_parity_lanecell PROPERTIES ENVIRONMENT "STRATA_ATTN_LANECELL=1")
    # fork: the Daily's decode attention (STRATA_ATTN_DAILY=1: PR #540's chunk kernel on every card and the bounded
    # merge) against upstream's kernels, bitwise; the production entries checked with the variable off and on
    add_executable(qsa_merge_daily_parity src/kernels/qsa_merge_daily_parity.cpp)
    target_include_directories(qsa_merge_daily_parity PRIVATE ${CMAKE_CUDA_TOOLKIT_INCLUDE_DIRECTORIES})
    target_link_libraries(qsa_merge_daily_parity PRIVATE strata_kernels CUDA::cudart)
    add_test(NAME qsa_merge_daily_parity_default COMMAND qsa_merge_daily_parity)
    add_test(NAME qsa_merge_daily_parity_daily COMMAND qsa_merge_daily_parity)
    set_tests_properties(qsa_merge_daily_parity_default PROPERTIES ENVIRONMENT "STRATA_ATTN_DAILY=0"
                         SKIP_RETURN_CODE 77)
    set_tests_properties(qsa_merge_daily_parity_daily PROPERTIES ENVIRONMENT "STRATA_ATTN_DAILY=1"
                         SKIP_RETURN_CODE 77)
    # the prompt path's block scores on the thread-per-head kernel (Turing) against the warp kernel, bitwise
    add_executable(qsa_scores_parity src/kernels/qsa_scores_parity.cpp)
    target_include_directories(qsa_scores_parity PRIVATE ${CMAKE_CUDA_TOOLKIT_INCLUDE_DIRECTORIES})
    target_link_libraries(qsa_scores_parity PRIVATE strata_kernels CUDA::cudart)
    add_test(NAME qsa_scores_parity COMMAND qsa_scores_parity)
    set_tests_properties(qsa_scores_parity PROPERTIES SKIP_RETURN_CODE 77 ENVIRONMENT "STRATA_SCORES_THREAD=1")
    # QSA selection: the default block scorer against STRATA_QSA_ROWS=1 (block_scores_rows_kernel), scores and
    # top-k ids bit for bit.  The driver re-executes the binary twice (the dispatch is read once per process).
    # The .cpp holds the synthetic-data kernels, so it is compiled as CUDA.
    add_executable(qsa_select_rows_parity_test tests/cuda/qsa_select_rows_parity_test.cpp)
    set_source_files_properties(tests/cuda/qsa_select_rows_parity_test.cpp PROPERTIES LANGUAGE CUDA)
    target_include_directories(qsa_select_rows_parity_test PRIVATE ${CMAKE_CUDA_TOOLKIT_INCLUDE_DIRECTORIES})
    target_link_libraries(qsa_select_rows_parity_test PRIVATE strata_kernels CUDA::cudart)
    add_test(NAME qsa_select_rows_parity_test COMMAND qsa_select_rows_parity_test)
    set_tests_properties(qsa_select_rows_parity_test PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 120)
  endif()
  # QSAROWS (docs/QSAROWS.md): what the T rows of a verification window cost in a QSA layer's decode kernels at a long
  # context (block scores, top-k, attention over each row's selection, indexer append), row by row; GPU, synthetic, no
  # model.  Not in the default build: `cmake --build <dir> --target qsa_rows_bench`.
  if(STRATA_ENABLE_CUDA)
    add_executable(qsa_rows_bench EXCLUDE_FROM_ALL src/kernels/qsa_rows_bench.cpp)
    target_include_directories(qsa_rows_bench PRIVATE ${CMAKE_CUDA_TOOLKIT_INCLUDE_DIRECTORIES})
    target_link_libraries(qsa_rows_bench PRIVATE strata_kernels CUDA::cudart)
  endif()
  # the same on CUDA: the tensor-core scorer (sm_80+) or the FP32 tiled one (below sm_80, and forced on any card by
  # STRATA_QSA_WARP=1 in the second test) against the warp kernel and FP64, at 32K and 131K
  if(STRATA_BUILD_TESTS AND STRATA_ENABLE_CUDA)
    add_executable(qsa_select_bench src/kernels/qsa_select_bench.cpp)
    target_include_directories(qsa_select_bench PRIVATE ${CMAKE_CUDA_TOOLKIT_INCLUDE_DIRECTORIES})
    target_link_libraries(qsa_select_bench PRIVATE strata_kernels CUDA::cudart)
    add_test(NAME qsa_select_bench COMMAND qsa_select_bench 32768 256 3 262144)
    add_test(NAME qsa_select_bench_fp32_tiled COMMAND qsa_select_bench 131072 256 3 262144)
    set_tests_properties(qsa_select_bench_fp32_tiled PROPERTIES ENVIRONMENT "STRATA_QSA_WARP=1;STRATA_SELECT_SIMT=1")
  endif()
  if(STRATA_BUILD_TESTS AND STRATA_ENABLE_CUDA)   # the prompt attention on a recorded selection (STRATA_QSA_DUMP)
    add_executable(pa_bench tests/cuda/pa_bench.cpp)
    target_include_directories(pa_bench PRIVATE ${CMAKE_CUDA_TOOLKIT_INCLUDE_DIRECTORIES})
    target_link_libraries(pa_bench PRIVATE strata_kernels CUDA::cudart)
  endif()
  # plan v0.3 P6: llama.cpp's block layouts and codebook grids for the i-quant formats (MIT, third_party/ggml)
  target_include_directories(strata_kernels PRIVATE third_party/ggml)
  # Match the pinned llama.cpp CUDA arithmetic contract for this adapter only.
  set_source_files_properties(src/kernels/cuda/native_mmvq.cu src/kernels/cuda/native_bf16.cu
    src/kernels/cuda/native_gr_norm.cu
    src/kernels/cuda/native_moe.cu
    src/kernels/cuda/native_gdn.cu
    src/kernels/cuda/native_gr_postops.cu
    src/kernels/cuda/native_router.cu
    src/kernels/cuda/native_gdn_preprocess.cu
    src/kernels/cuda/native_qsa.cu
    src/kernels/cuda/native_rope.cu
    src/kernels/cuda/native_ple_postops.cu
    src/kernels/cuda/native_flash_attn.cu
    src/kernels/cuda/native_qsa_indexer.cu
    PROPERTIES COMPILE_OPTIONS "--use_fast_math")

  set(STRATA_ORACLE_SOURCE_DIR "" CACHE PATH "Pinned llama.cpp source for independent CUDA parity tests")
  set(STRATA_ORACLE_BUILD_DIR "" CACHE PATH "Matching existing shared llama.cpp build")
  if(STRATA_ORACLE_SOURCE_DIR AND STRATA_ORACLE_BUILD_DIR)
    foreach(parity IN ITEMS native_mmvq native_bf16 native_shared native_gr_norm native_moe native_gdn native_gr_postops native_router native_gdn_preprocess native_gdn_pipeline native_qsa native_rope native_ple_postops native_flash_attn native_qsa_indexer)
    add_executable(${parity}_parity bench/micro/${parity}_parity.cpp)
    target_include_directories(${parity}_parity PRIVATE
      "${STRATA_ORACLE_SOURCE_DIR}/ggml/include" ${_strata_gpu_include_directories})
    target_compile_definitions(${parity}_parity PRIVATE GGML_SHARED GGML_BACKEND_SHARED)
    target_link_libraries(${parity}_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
    foreach(component IN ITEMS ggml ggml-base ggml-cpu ggml-cuda)
      string(REPLACE "-" "_" variable "STRATA_ORACLE_${component}")
      find_library(${variable} NAMES ${component} PATHS
        "${STRATA_ORACLE_BUILD_DIR}/ggml/src" "${STRATA_ORACLE_BUILD_DIR}/ggml/src/${component}"
        "${STRATA_ORACLE_BUILD_DIR}/bin" NO_DEFAULT_PATH REQUIRED)
      target_link_libraries(${parity}_parity PRIVATE "${${variable}}")
    endforeach()
    add_test(NAME ${parity}_parity COMMAND ${parity}_parity --selftest)
    endforeach()
  endif()

  # ---- strata_engine (P2.S5) -----------------------------------------------------
  # The COMPOSED layer lives here and not in `strata_core`, because it calls kernels and `strata_kernels`
  # already links `strata_core` - putting `gdn_layer` in core would make the two mutually dependent.  A third
  # library is the honest shape of the dependency graph: core knows about devices and weights, kernels know
  # about arithmetic, and this one knows the ORDER the arithmetic happens in.
  add_library(strata_engine STATIC src/core/layer.cpp src/core/doorbell.cpp src/core/graph_capture.cpp src/core/qsa_state.cpp src/core/session.cpp src/core/expert_source.cpp src/core/remote_experts.cpp
    src/core/remote_expert_opt.cu
    src/core/expert_cache.cpp src/core/native_head.cpp src/core/native_dense.cpp src/core/native_dense_scratch.cpp
    src/core/verify.cpp src/core/mtp.cpp src/core/dflash2.cpp
    src/core/conversation_copy.cpp src/core/conversation_snapshot.cpp src/core/conversation_state.cpp
    src/core/conversation_memory.cpp src/core/peer_experts.cpp
    src/core/vmm.cpp src/core/conversation_file.cpp)
  target_include_directories(strata_engine PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
  target_link_libraries(strata_engine PUBLIC strata_core strata_kernels strata_kernels_cpu ${_strata_gpu_runtime_target})
  if(STRATA_BUILD_TESTS)
    if(UNIX AND CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
      add_executable(gpu_event_pairs_test
        tests/platform/gpu_event_pairs_test.cpp src/platform/gpu_event_pairs.cpp)
      target_include_directories(gpu_event_pairs_test PRIVATE
        include ${_strata_gpu_include_directories})
      target_link_options(gpu_event_pairs_test PRIVATE
        -Wl,--wrap=cudaGetDevice -Wl,--wrap=cudaSetDevice
        -Wl,--wrap=cudaEventCreateWithFlags -Wl,--wrap=cudaEventDestroy
        -Wl,--wrap=cudaEventRecord -Wl,--wrap=cudaEventSynchronize -Wl,--wrap=cudaEventElapsedTime
        -Wl,--wrap=cudaGetLastError)
      target_link_libraries(gpu_event_pairs_test PRIVATE strata_warnings)
      add_test(NAME gpu_event_pairs_test COMMAND gpu_event_pairs_test)
      set_tests_properties(gpu_event_pairs_test PROPERTIES
        ENVIRONMENT "CUDA_VISIBLE_DEVICES=-1" TIMEOUT 30)
      add_executable(weights_load_transaction_test
        tests/core/weights_load_transaction_test.cpp src/core/weights.cpp src/platform/memory.cpp)
      target_include_directories(weights_load_transaction_test PRIVATE
        include ${_strata_gpu_include_directories})
      target_link_options(weights_load_transaction_test PRIVATE
        -Wl,--wrap=cudaHostAlloc -Wl,--wrap=cudaFreeHost
        -Wl,--wrap=cudaMemcpy -Wl,--wrap=cudaDeviceSynchronize
        -Wl,--wrap=cudaGetDevice -Wl,--wrap=cudaSetDevice
        -Wl,--wrap=cudaFree -Wl,--wrap=cudaGetLastError)
      target_link_libraries(weights_load_transaction_test PRIVATE strata_warnings ${_strata_gpu_runtime_target})
      add_test(NAME weights_load_transaction_test COMMAND weights_load_transaction_test)
      set_tests_properties(weights_load_transaction_test PROPERTIES
        ENVIRONMENT "CUDA_VISIBLE_DEVICES=-1" TIMEOUT 30)
      add_executable(native_dense_scratch_test
        tests/platform/native_dense_scratch_test.cpp src/core/native_dense_scratch.cpp)
      target_include_directories(native_dense_scratch_test PRIVATE
        include ${_strata_gpu_include_directories})
      target_link_options(native_dense_scratch_test PRIVATE
        -Wl,--wrap=cudaGetDevice -Wl,--wrap=cudaSetDevice
        -Wl,--wrap=cudaMalloc -Wl,--wrap=cudaFree
        -Wl,--wrap=cudaEventCreateWithFlags -Wl,--wrap=cudaEventDestroy
        -Wl,--wrap=cudaEventRecord -Wl,--wrap=cudaStreamWaitEvent
        -Wl,--wrap=cudaDeviceSynchronize -Wl,--wrap=cudaGetLastError)
      target_link_libraries(native_dense_scratch_test PRIVATE strata_warnings)
      add_test(NAME native_dense_scratch_test COMMAND native_dense_scratch_test)
      set_tests_properties(native_dense_scratch_test PROPERTIES
        ENVIRONMENT "CUDA_VISIBLE_DEVICES=-1" TIMEOUT 30)
      add_executable(doorbell_transaction_test
        tests/core/doorbell_transaction_test.cpp src/core/doorbell.cpp)
      target_include_directories(doorbell_transaction_test PRIVATE
        include ${_strata_gpu_include_directories})
      target_link_options(doorbell_transaction_test PRIVATE
        -Wl,--wrap=cudaHostAlloc -Wl,--wrap=cudaFreeHost
        -Wl,--wrap=cudaHostGetDevicePointer)
      target_link_libraries(doorbell_transaction_test PRIVATE strata_warnings)
      add_test(NAME doorbell_transaction_test COMMAND doorbell_transaction_test)
      set_tests_properties(doorbell_transaction_test PROPERTIES
        ENVIRONMENT "CUDA_VISIBLE_DEVICES=-1" TIMEOUT 30)
      add_executable(graph_capture_test
        tests/core/graph_capture_test.cpp src/core/graph_capture.cpp)
      target_include_directories(graph_capture_test PRIVATE
        include ${_strata_gpu_include_directories})
      target_link_options(graph_capture_test PRIVATE
        -Wl,--wrap=cudaStreamCreate -Wl,--wrap=cudaStreamDestroy
        -Wl,--wrap=cudaStreamBeginCapture -Wl,--wrap=cudaStreamEndCapture
        -Wl,--wrap=cudaGraphInstantiate -Wl,--wrap=cudaGraphDestroy
        -Wl,--wrap=cudaGetErrorString)
      target_link_libraries(graph_capture_test PRIVATE strata_warnings)
      add_test(NAME graph_capture_test COMMAND graph_capture_test)
      set_tests_properties(graph_capture_test PROPERTIES
        ENVIRONMENT "CUDA_VISIBLE_DEVICES=-1" TIMEOUT 30)
      add_executable(qsa_state_resource_test
        tests/core/qsa_state_resource_test.cpp src/core/qsa_state.cpp)
      target_include_directories(qsa_state_resource_test PRIVATE
        include ${_strata_gpu_include_directories})
      target_link_options(qsa_state_resource_test PRIVATE
        -Wl,--wrap=cudaFreeHost -Wl,--wrap=cudaHostUnregister)
      target_link_libraries(qsa_state_resource_test PRIVATE strata_warnings)
      add_test(NAME qsa_state_resource_test COMMAND qsa_state_resource_test)
      set_tests_properties(qsa_state_resource_test PROPERTIES
        ENVIRONMENT "CUDA_VISIBLE_DEVICES=-1" TIMEOUT 30)
    endif()
    add_executable(host_graph_launch_test tests/core/host_graph_launch_test.cu)
    target_include_directories(host_graph_launch_test PRIVATE include)
    target_link_libraries(host_graph_launch_test PRIVATE CUDA::cudart Threads::Threads)
    add_test(NAME host_graph_launch_test COMMAND host_graph_launch_test)
    set_tests_properties(host_graph_launch_test PROPERTIES TIMEOUT 30)
  endif()
  if(STRATA_BUILD_TESTS AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/exchange_storage_test.cpp")
    add_executable(exchange_storage_test tests/core/exchange_storage_test.cpp)
    target_include_directories(exchange_storage_test PRIVATE include)
    target_link_libraries(exchange_storage_test PRIVATE Threads::Threads)
    add_test(NAME exchange_storage_test COMMAND exchange_storage_test)
    set_tests_properties(exchange_storage_test PROPERTIES TIMEOUT 60)
  endif()

  # available_host_bytes(): MemAvailable, cgroup limits and the operator's --memory-limit-mib cap on fixture directories
  # - no GPU, no CUDA
  if(STRATA_BUILD_TESTS AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/memory_guard_test.cpp")
    add_executable(memory_guard_test tests/core/memory_guard_test.cpp src/core/conversation_memory.cpp)
    target_include_directories(memory_guard_test PRIVATE include)
    add_test(NAME memory_guard_test COMMAND memory_guard_test)
    set_tests_properties(memory_guard_test PROPERTIES TIMEOUT 60)
  endif()

  if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/file_expert_source_test.cpp")
    enable_testing()
    add_executable(file_expert_source_test tests/core/file_expert_source_test.cpp)
    target_link_libraries(file_expert_source_test PRIVATE strata_engine)
    add_test(NAME file_expert_source_test COMMAND file_expert_source_test)
    set_tests_properties(file_expert_source_test PROPERTIES TIMEOUT 60)
    add_executable(vmm_test tests/core/vmm_test.cpp)
    target_link_libraries(vmm_test PRIVATE strata_engine)
    add_test(NAME vmm_test COMMAND vmm_test)
    set_tests_properties(vmm_test PROPERTIES TIMEOUT 60)
    # The resident RAM mode with a layer split: the copy keeps each stage's lend region, and the experts the
    # prompt path borrows are served from RAM (the file source reads none of them).  The device calls are
    # wrapped - conversation_transfer_test's pattern - and `pin = false` keeps the copy pageable, so this
    # runs on a CPU-only PC.
    if(UNIX AND CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
      add_executable(split_lend_complement_test tests/core/split_lend_complement_test.cpp)
      target_link_libraries(split_lend_complement_test PRIVATE strata_engine)
      target_link_options(split_lend_complement_test PRIVATE
        -Wl,--wrap=cudaMalloc -Wl,--wrap=cudaFree -Wl,--wrap=cudaMemset -Wl,--wrap=cudaMemcpy
        -Wl,--wrap=cudaMemcpyAsync -Wl,--wrap=cudaStreamSynchronize -Wl,--wrap=cudaDeviceSynchronize
        -Wl,--wrap=cudaGetLastError -Wl,--wrap=cudaGetDevice -Wl,--wrap=cudaSetDevice
        -Wl,--wrap=cudaMemGetInfo -Wl,--wrap=cudaHostAlloc -Wl,--wrap=cudaFreeHost)
      add_test(NAME split_lend_complement_test COMMAND split_lend_complement_test)
      set_tests_properties(split_lend_complement_test PROPERTIES ENVIRONMENT "CUDA_VISIBLE_DEVICES=-1" TIMEOUT 120)
    endif()
  endif()
  # #477: the learned expert profile (--expert-profile-save): its order and make_profile.py's file format (CPU only)
  if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/expert_profile_save_test.cpp")
    enable_testing()
    add_executable(expert_profile_save_test tests/core/expert_profile_save_test.cpp)
    target_link_libraries(expert_profile_save_test PRIVATE strata_engine)
    add_test(NAME expert_profile_save_test COMMAND expert_profile_save_test)
    set_tests_properties(expert_profile_save_test PROPERTIES TIMEOUT 60)
  endif()
  # the profile header's version is enforced: a version other than 1 is refused by its number (CPU only)
  if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/expert_profile_version_test.cpp")
    enable_testing()
    add_executable(expert_profile_version_test tests/core/expert_profile_version_test.cpp)
    target_link_libraries(expert_profile_version_test PRIVATE strata_engine)
    add_test(NAME expert_profile_version_test COMMAND expert_profile_version_test)
    set_tests_properties(expert_profile_version_test PROPERTIES TIMEOUT 60)
  endif()
  if(STRATA_BUILD_TESTS AND STRATA_NATIVE_EXPERTS)
    # native_experts.txt v3-v4 (the production format and the per-role shard column) and the arena filled from
    # synthetic GGUF shards at the real expert dimensions
    add_executable(expert_layout_test tests/core/expert_layout_test.cpp)
    target_link_libraries(expert_layout_test PRIVATE strata_engine)
    add_test(NAME expert_layout_test COMMAND expert_layout_test)
    set_tests_properties(expert_layout_test PROPERTIES TIMEOUT 60)
    # #326: a native pack's BF16 (--compat-bf16) PLE key stays in the arena; a quantized key row loads as before
    add_executable(native_dense_ple_key_test tests/core/native_dense_ple_key_test.cpp)
    target_link_libraries(native_dense_ple_key_test PRIVATE strata_engine)
    add_test(NAME native_dense_ple_key_test COMMAND native_dense_ple_key_test)
    set_tests_properties(native_dense_ple_key_test PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
    # #369: per-layer admission on a sized (native pack) cache starts each layer in its own range
    add_executable(expert_cache_per_layer_test tests/core/expert_cache_per_layer_test.cpp)
    target_link_libraries(expert_cache_per_layer_test PRIVATE strata_engine)
    add_test(NAME expert_cache_per_layer_test COMMAND expert_cache_per_layer_test)
    set_tests_properties(expert_cache_per_layer_test PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
    add_executable(expert_cache_segmented_test tests/core/expert_cache_segmented_test.cpp)
    target_link_libraries(expert_cache_segmented_test PRIVATE strata_engine)
    add_test(NAME expert_cache_segmented_test COMMAND expert_cache_segmented_test)
    set_tests_properties(expert_cache_segmented_test PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
  endif()
  if(STRATA_BUILD_CONVERSATION_TESTS)
    add_executable(conversation_snapshot_test src/core/conversation_snapshot_test.cpp)
    target_link_libraries(conversation_snapshot_test PRIVATE strata_engine)
    add_test(NAME conversation_snapshot_test COMMAND conversation_snapshot_test)
    set_tests_properties(conversation_snapshot_test PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 120)
    add_executable(conversation_validation_test src/core/conversation_validation_test.cpp)
    target_link_libraries(conversation_validation_test PRIVATE strata_engine)
    add_test(NAME conversation_validation_test COMMAND conversation_validation_test)
    set_tests_properties(conversation_validation_test PROPERTIES ENVIRONMENT "CUDA_VISIBLE_DEVICES=-1" TIMEOUT 120)
    if(UNIX AND CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
      add_executable(conversation_copy_resources_test
        tests/core/conversation_copy_resources_test.cpp src/core/conversation_copy.cpp)
      target_include_directories(conversation_copy_resources_test PRIVATE
        include ${_strata_gpu_include_directories})
      find_package(Threads REQUIRED)
      target_link_libraries(conversation_copy_resources_test PRIVATE Threads::Threads strata_warnings)
      target_link_options(conversation_copy_resources_test PRIVATE
        -Wl,--wrap=cudaGetDevice -Wl,--wrap=cudaSetDevice
        -Wl,--wrap=cudaStreamCreateWithFlags -Wl,--wrap=cudaStreamSynchronize
        -Wl,--wrap=cudaStreamDestroy -Wl,--wrap=cudaStreamWaitEvent
        -Wl,--wrap=cudaEventCreateWithFlags -Wl,--wrap=cudaEventRecord
        -Wl,--wrap=cudaEventDestroy -Wl,--wrap=cudaMemcpyAsync
        -Wl,--wrap=cudaGetLastError)
      add_test(NAME conversation_copy_resources_test COMMAND conversation_copy_resources_test)
      set_tests_properties(conversation_copy_resources_test PROPERTIES
        ENVIRONMENT "CUDA_VISIBLE_DEVICES=-1" TIMEOUT 60)
      add_executable(conversation_transfer_test src/core/conversation_validation_test.cpp)
      target_compile_definitions(conversation_transfer_test PRIVATE CONVERSATION_TEST_TRANSFERS)
      target_link_options(conversation_transfer_test PRIVATE
        -Wl,--wrap=cudaMemcpy -Wl,--wrap=cudaMemcpyAsync -Wl,--wrap=cudaStreamSynchronize
        -Wl,--wrap=cudaGetDevice -Wl,--wrap=cudaStreamCreateWithFlags
        -Wl,--wrap=cudaDeviceSynchronize -Wl,--wrap=cudaGetLastError)
      target_link_libraries(conversation_transfer_test PRIVATE strata_engine)
      add_test(NAME conversation_transfer_test COMMAND conversation_transfer_test)
      set_tests_properties(conversation_transfer_test PROPERTIES ENVIRONMENT "CUDA_VISIBLE_DEVICES=-1" TIMEOUT 120)
    endif()
  endif()

  # `strata generate` (P2.S6).  The FIRST user-facing program: everything under it is a component, and this is
  # what composes them into a token.  No tokenizer - the prompt arrives as ids - because Gate C1 compares logits
  # against llama.cpp on the SAME ids and a tokenizer on one side of that comparison is a second variable.
  # plan v0.3 P5: batched prompt processing (tensor-core GEMMs through cuBLAS).
  add_library(strata_prefill STATIC src/prefill/gemm.cu src/prefill/kernels.cu src/prefill/prefill.cpp)
  target_include_directories(strata_prefill PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include ${_strata_gpu_include_directories})
  target_link_libraries(strata_prefill PUBLIC strata_engine ${_strata_gpu_blas_target} ${_strata_gpu_runtime_target})

  # DFlash2 parity and timing tools are opt-in build targets.
  add_executable(dflash2_parity EXCLUDE_FROM_ALL src/core/dflash2_parity.cpp)
  target_include_directories(dflash2_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(dflash2_parity PRIVATE strata_engine strata_kernels ${_strata_gpu_runtime_target})
  add_executable(dflash2_gemm_bench EXCLUDE_FROM_ALL src/core/dflash2_gemm_bench.cpp)
  target_include_directories(dflash2_gemm_bench PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(dflash2_gemm_bench PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_executable(dflash2_launch_bench EXCLUDE_FROM_ALL src/core/dflash2_launch_bench.cu)
  target_include_directories(dflash2_launch_bench PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(dflash2_launch_bench PRIVATE ${_strata_gpu_runtime_target})
  add_executable(dflash2_bw_bench EXCLUDE_FROM_ALL src/core/dflash2_bw_bench.cu)
  target_include_directories(dflash2_bw_bench PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(dflash2_bw_bench PRIVATE ${_strata_gpu_runtime_target})
  add_executable(dflash2_small_bench EXCLUDE_FROM_ALL src/core/dflash2_small_bench.cpp)
  target_include_directories(dflash2_small_bench PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(dflash2_small_bench PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_executable(dflash2_attn_bench EXCLUDE_FROM_ALL src/core/dflash2_attn_bench.cpp)
  target_include_directories(dflash2_attn_bench PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(dflash2_attn_bench PRIVATE strata_kernels ${_strata_gpu_runtime_target})

  add_executable(strata src/program/generate.cpp)
  target_include_directories(strata PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(strata PRIVATE strata_engine strata_prefill strata_spec ${_strata_gpu_runtime_target})

  add_executable(dequant_s2_parity src/kernels/dequant_s2_parity.cpp)
  # The tests are HOST C++ files that call cudaMalloc/cudaMemcpy themselves, so they need the toolkit headers
  # and cudart explicitly: enabling the CUDA language gives those to .cu sources automatically, but a .cpp
  # target is just a C++ target and gets neither.
  target_include_directories(dequant_s2_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(dequant_s2_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME dequant_s2_parity COMMAND dequant_s2_parity --selftest)

  add_executable(s2_gemv_parity src/kernels/s2_gemv_parity.cpp)
  target_include_directories(s2_gemv_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(s2_gemv_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME s2_gemv_parity COMMAND s2_gemv_parity --selftest)

  add_executable(iq_parity src/kernels/iq_parity.cpp)
  target_include_directories(iq_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(iq_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  # TODO 24: the i-quant fixtures are generated, not shipped.  tools/iq_fixture.py needs python3 + numpy
  # and the repository's vendored gguf-py (no pip installs); it writes deterministic fixtures into the
  # BUILD directory (the source tree is not touched).  The CTest fixture makes generation-to-test order
  # explicit even under parallel execution; a fixture-generation failure fails iq_parity with it.
  find_package(Python3 COMPONENTS Interpreter QUIET)
  # the generator needs an interpreter WITH numpy: probe the configured one, the setup's .venv, and the
  # system python3; -DIQ_FIXTURE_PYTHON=... overrides the search outright.
  set(_iq_python "")
  set(_iq_candidates "")
  if(IQ_FIXTURE_PYTHON)
    list(APPEND _iq_candidates ${IQ_FIXTURE_PYTHON})
  else()
    if(Python3_Interpreter_FOUND)
      list(APPEND _iq_candidates ${Python3_EXECUTABLE})
    endif()
    list(APPEND _iq_candidates "${CMAKE_CURRENT_SOURCE_DIR}/.venv/bin/python"
                                "${CMAKE_CURRENT_SOURCE_DIR}/.venv/Scripts/python.exe"
                                /usr/bin/python3
                                python3)
  endif()
  foreach(_cand IN LISTS _iq_candidates)
    if(_cand STREQUAL "")
      continue()
    endif()
    execute_process(COMMAND ${_cand} -c "import numpy" OUTPUT_QUIET ERROR_QUIET RESULT_VARIABLE _iq_probe)
    if(_iq_probe EQUAL 0)
      set(_iq_python "${_cand}")
      break()
    endif()
  endforeach()
  if(NOT _iq_python STREQUAL "")
    add_test(NAME iq_parity_fixtures
             COMMAND ${_iq_python} ${CMAKE_CURRENT_SOURCE_DIR}/tools/iq_fixture.py
                     --out ${CMAKE_CURRENT_BINARY_DIR}/iq_fixture)
    # exit 3: numpy or the vendored gguf-py is missing (a trimmed third_party) - a skip, not a failure
    set_tests_properties(iq_parity_fixtures PROPERTIES FIXTURES_SETUP iq_fixture_data SKIP_RETURN_CODE 3)
    add_test(NAME iq_parity COMMAND iq_parity ${CMAKE_CURRENT_BINARY_DIR}/iq_fixture)
    set_tests_properties(iq_parity PROPERTIES FIXTURES_REQUIRED iq_fixture_data SKIP_RETURN_CODE 3)
  else()
    message(STATUS "STRATA tests: no python3 with numpy was found (the configured interpreter and .venv "
                   "were probed) - the iq_parity fixtures and its test are not registered; the fixtures "
                   "need python3 with numpy and the repository's vendored gguf-py")
  endif()
  if(STRATA_ENABLE_CUDA)
    # native_mmvq's multi_exact contract, bitwise: a T-column call against T single-column calls on the same Q8_1
    # bytes, with the flag off as the negative control (synthetic, GPU, no model; the control assumes 32-lane warps)
    add_executable(mmvq_multi_parity src/kernels/mmvq_multi_parity.cpp)
    target_include_directories(mmvq_multi_parity PRIVATE ${_strata_gpu_include_directories})
    target_link_libraries(mmvq_multi_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
    add_test(NAME mmvq_multi_parity COMMAND mmvq_multi_parity)
  endif()
  if(STRATA_ENABLE_CUDA)
    # fork F4: the 2-4 column dense projections from the interleaved q8_1 copy, bitwise vs native_mmvq (GPU, synthetic)
    add_executable(mmvq_il_parity src/kernels/mmvq_il_parity.cpp)
    target_include_directories(mmvq_il_parity PRIVATE ${_strata_gpu_include_directories})
    target_link_libraries(mmvq_il_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
    add_test(NAME mmvq_il_parity COMMAND mmvq_il_parity)
    # the wide dequant kernels (a thread per 8 values; sm_61's default), bitwise vs the per-group ones (GPU, synthetic,
    # no model; --bench adds old/new timings)
    add_executable(dequant_wide_parity src/kernels/dequant_wide_parity.cpp)
    target_include_directories(dequant_wide_parity PRIVATE ${_strata_gpu_include_directories})
    target_link_libraries(dequant_wide_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
    add_test(NAME dequant_wide_parity COMMAND dequant_wide_parity)
  endif()
  # the IQ kernels that decode a weight part once for every column/entry, bitwise vs the per-column ones
  # (GPU, synthetic, no model; --bench adds old/new timings)
  add_executable(iq_multi_parity src/kernels/iq_multi_parity.cpp)
  target_include_directories(iq_multi_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(iq_multi_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME iq_multi_parity COMMAND iq_multi_parity)
  # the prompt path's DeltaNet recurrence: gdn_rec_kh_kernel against gdn_rec_cols_pipe_kernel, bitwise (GPU,
  # synthetic, no model; --bench adds timings).
  if(STRATA_ENABLE_CUDA)
    # Independent pinned expert uploads: byte parity and stream/event ordering.
    add_executable(dma_batch_parity tests/core/dma_batch_parity.cu)
    target_include_directories(dma_batch_parity PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_link_libraries(dma_batch_parity PRIVATE ${_strata_gpu_runtime_target})
    add_test(NAME dma_batch_parity COMMAND dma_batch_parity)
    set_tests_properties(dma_batch_parity PROPERTIES TIMEOUT 120)
    add_executable(gdn_rec_parity src/prefill/gdn_rec_parity.cu)
    target_link_libraries(gdn_rec_parity PRIVATE ${_strata_gpu_runtime_target})
    add_test(NAME gdn_rec_parity COMMAND gdn_rec_parity)
    # the prompt path's BF16 products against cuBLAS's own BF16 GEMM, within fp32 rounding (sm_7x runs them on the
    # FP16 tensor cores; GPU, synthetic, no model; --bench adds timings)
    add_executable(gemm_bf16_parity src/prefill/gemm_bf16_parity.cu)
    target_link_libraries(gemm_bf16_parity PRIVATE strata_prefill )
    add_test(NAME gemm_bf16_parity COMMAND gemm_bf16_parity)
  endif()
  # native_expert_grouped's group stride and fused SwiGLU + q8_1, bitwise vs the launches before them (GPU, synthetic,
  # no model; --bench adds v1/new timings of a window's 48 calls in a graph)
  add_executable(native_grouped_parity src/kernels/native_grouped_parity.cpp)
  target_include_directories(native_grouped_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(native_grouped_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME native_grouped_parity COMMAND native_grouped_parity)
  add_executable(mmvf_rows_parity src/kernels/mmvf_rows_parity.cpp)
  target_include_directories(mmvf_rows_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(mmvf_rows_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME mmvf_rows_parity COMMAND mmvf_rows_parity)
  set_tests_properties(mmvf_rows_parity PROPERTIES ENVIRONMENT "STRATA_MMVF_ROWS=1" TIMEOUT 120)
  add_executable(shared_expert_parity src/kernels/shared_expert_parity.cpp)
  target_include_directories(shared_expert_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(shared_expert_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME shared_expert_parity COMMAND shared_expert_parity --selftest)
  # the split / batched verify-window kernels (from eddoursul's fork), bitwise vs the kernels they replace; CUDA only
  if(STRATA_ENABLE_CUDA)
    add_executable(verify_parity src/kernels/verify_parity.cpp)
    target_include_directories(verify_parity PRIVATE ${_strata_gpu_include_directories})
    target_link_libraries(verify_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
    add_test(NAME verify_parity COMMAND verify_parity --selftest)
  endif()
  add_executable(fused_gr_bench src/kernels/fused_gr_bench.cpp)
  target_include_directories(fused_gr_bench PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(fused_gr_bench PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_executable(router_chain_bench src/kernels/router_chain_bench.cpp)
  target_include_directories(router_chain_bench PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(router_chain_bench PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  # B6: the dense projections' time per verify row at the model's shapes, default vs STRATA_B6_MMVQ_ROWS, bitwise
  if(STRATA_ENABLE_CUDA)
    add_executable(b6_mmvq_bench src/kernels/b6_mmvq_bench.cpp)
    target_include_directories(b6_mmvq_bench PRIVATE ${_strata_gpu_include_directories})
    target_link_libraries(b6_mmvq_bench PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  endif()
  add_executable(gr_parity src/kernels/gr_parity.cpp)
  target_include_directories(gr_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(gr_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME gr_parity COMMAND gr_parity --selftest)

  add_executable(gdn_parity src/kernels/gdn_parity.cpp)
  target_include_directories(gdn_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(gdn_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME gdn_parity COMMAND gdn_parity --selftest)

  add_executable(s2_gemv_q8_parity src/kernels/s2_gemv_q8_parity.cpp)
  target_include_directories(s2_gemv_q8_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(s2_gemv_q8_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME s2_gemv_q8_parity COMMAND s2_gemv_q8_parity --selftest)

  add_executable(sampler_parity src/kernels/sampler_parity.cpp)
  target_include_directories(sampler_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(sampler_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME sampler_parity COMMAND sampler_parity --selftest)
  set_tests_properties(sampler_parity PROPERTIES ENVIRONMENT "STRATA_OLD_SAMPLER=0;STRATA_SAMPLER_ONE_BLOCK=0")
  # the GPU verifier of probabilistic draft acceptance against the host reference (STRATA_SPEC_PROB)
  add_executable(spec_verify_parity src/kernels/spec_verify_parity.cpp)
  target_include_directories(spec_verify_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(spec_verify_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME spec_verify_parity COMMAND spec_verify_parity)
  set_tests_properties(spec_verify_parity PROPERTIES ENVIRONMENT "STRATA_OLD_SAMPLER=0;STRATA_SAMPLER_ONE_BLOCK=0")
  # the same fixtures on the other two sampled paths: the one-block kernel and the 0.1.20 kernel
  add_test(NAME sampler_parity_one_block COMMAND sampler_parity --selftest)
  set_tests_properties(sampler_parity_one_block PROPERTIES ENVIRONMENT "STRATA_OLD_SAMPLER=0;STRATA_SAMPLER_ONE_BLOCK=1")
  add_test(NAME sampler_parity_old COMMAND sampler_parity --selftest)
  set_tests_properties(sampler_parity_old PROPERTIES ENVIRONMENT "STRATA_OLD_SAMPLER=1;STRATA_SAMPLER_ONE_BLOCK=0")
  add_executable(rope_parity src/kernels/rope_parity.cpp)
  target_include_directories(rope_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(rope_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME rope_parity COMMAND rope_parity --selftest)

  add_executable(quantize_act_parity src/kernels/quantize_act_parity.cpp)
  target_include_directories(quantize_act_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(quantize_act_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME quantize_act_parity COMMAND quantize_act_parity --selftest)

  # the verify window's routing in one multi-column BF16 projection + one top-k, bitwise the per-token calls
  add_executable(route_window_parity src/kernels/route_window_parity.cpp)
  target_include_directories(route_window_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(route_window_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME route_window_parity COMMAND route_window_parity)

  # STRATA_ROUTE_RESIDENT's swap: the warp-per-token kernel against a scalar reference of the same semantics,
  # bitwise on the ids, the weights and the four counters (GPU, synthetic, no model)
  add_executable(route_resident_parity src/kernels/route_resident_parity.cu)
  target_include_directories(route_resident_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(route_resident_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME route_resident_parity COMMAND route_resident_parity)
  set_tests_properties(route_resident_parity PROPERTIES TIMEOUT 120)

  add_executable(router_top10_parity src/kernels/router_top10_parity.cpp)
  target_include_directories(router_top10_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(router_top10_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME router_top10_parity COMMAND router_top10_parity --selftest)
  # #783 PR-c: the multi-token router and the k = 10 float4 combine, bitwise against the single-token contracts
  add_executable(native_multi_parity src/kernels/native_multi_parity.cpp)
  target_include_directories(native_multi_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(native_multi_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME native_multi_parity COMMAND native_multi_parity)
  # #783 PR-d: the batched K/V append (INT8, Q4_0, the hybrid fold) against the per-cell appends, memcmp
  add_executable(kv_steps_parity src/kernels/kv_steps_parity.cpp)
  target_include_directories(kv_steps_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(kv_steps_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME kv_steps_parity COMMAND kv_steps_parity)
  # #783 PR-e: the device-built expert plan (parallel grouping + prefix scan) against a host replay of its contract
  add_executable(resident_plan_parity src/kernels/resident_plan_parity.cpp)
  target_include_directories(resident_plan_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(resident_plan_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME resident_plan_parity COMMAND resident_plan_parity)
  # #783 PR-g: the multi-token hyper-connection read/write (exact-T projections under them) against the single-token calls
  add_executable(gr_multi_parity src/kernels/gr_multi_parity.cpp)
  target_include_directories(gr_multi_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(gr_multi_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME gr_multi_parity COMMAND gr_multi_parity)
  # STRATA_HC_PERSIST=1: the persistent hyper-connection read against the default multi read (bounded, two runs bitwise),
  # and with the variable unset it never runs (a skip where the card cannot hold its 80 blocks, and on CUDA)
  add_executable(hc_persist_parity src/kernels/hc_persist_parity.cpp)
  target_include_directories(hc_persist_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(hc_persist_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME hc_persist_parity COMMAND hc_persist_parity)
  add_test(NAME hc_persist_default_off COMMAND hc_persist_parity --off)

  add_executable(s_gemv_parity src/kernels/s_gemv_parity.cpp)
  target_include_directories(s_gemv_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(s_gemv_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME s_gemv_parity COMMAND s_gemv_parity --selftest)

  add_executable(elementwise_parity src/kernels/elementwise_parity.cpp)
  target_include_directories(elementwise_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(elementwise_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME elementwise_parity COMMAND elementwise_parity --selftest)

  add_executable(bf16_gemv_parity src/kernels/bf16_gemv_parity.cpp)
  target_include_directories(bf16_gemv_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(bf16_gemv_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME bf16_gemv_parity COMMAND bf16_gemv_parity --selftest)

  # the V100 fusion work: every fused kernel against its unfused pipeline, bitwise, and the before/after
  # CUDA-event timings at decode shapes (`--bench`).  It is a .cu because it carries the unfused reference
  # kernels it compares against.
  if(STRATA_BUILD_TESTS AND STRATA_ENABLE_CUDA)
    add_executable(fusions_parity bench/micro/fusions_parity.cu)
    target_include_directories(fusions_parity PRIVATE ${_strata_gpu_include_directories})
    target_link_libraries(fusions_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
    add_test(NAME fusions_parity COMMAND fusions_parity)
  endif()

  add_executable(s_gemv_q8k_parity src/kernels/s_gemv_q8k_parity.cpp)
  target_include_directories(s_gemv_q8k_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(s_gemv_q8k_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME s_gemv_q8k_parity COMMAND s_gemv_q8k_parity --selftest)

  add_executable(qsa_parity src/kernels/qsa_parity.cpp)
  target_include_directories(qsa_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(qsa_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME qsa_parity COMMAND qsa_parity --selftest)

  # the QSA top-k the engine dispatches against the original kernel: identical ids on both sides of the register
  # kernel's reach, with tied and with equal scores (synthetic, no model)
  add_executable(qsa_topk_parity src/kernels/qsa_topk_parity.cpp)
  target_include_directories(qsa_topk_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(qsa_topk_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME qsa_topk_parity COMMAND qsa_topk_parity --selftest)
  # the register kernel from the smallest prompt batch: same ids at every case
  add_test(NAME qsa_topk_parity_reg_always COMMAND qsa_topk_parity --selftest)
  set_tests_properties(qsa_topk_parity_reg_always PROPERTIES ENVIRONMENT "STRATA_TOPK_REG_MIN_BLOCKS=0")

  # ---- P2.S4: the PLE n-gram hash, the IQ4_NL table read, and the PLE block ----
  # `ngram.cpp` is host-only but lives in `strata_kernels` because `ple.cu` shares its geometry constants, and
  # splitting them across two libraries would only add a layer.
  #
  # The pack, the original GGUF and the ggml capture are all REQUIRED inputs: this test's oracles are real
  # artifacts, so the working directory is pinned to the source tree and a missing input is a hard error
  # INSIDE the test.  A test that passes by finding nothing to compare is the failure mode this project keeps
  # re-learning.
  add_executable(ple_parity src/kernels/ple_parity.cpp)
  target_include_directories(ple_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(ple_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  set(STRATA_PLE_FIXTURE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/bench/micro" CACHE PATH
      "Directory containing required ple_in.bin and ple_out.bin parity fixtures")
  add_test(NAME ple_parity COMMAND ple_parity --selftest
    --in "${STRATA_PLE_FIXTURE_DIR}/ple_in.bin" --out "${STRATA_PLE_FIXTURE_DIR}/ple_out.bin"
    WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})

  # Plan v0.3 P7: INT8 KV storage vs a host reference and vs the FP16 path (GPU, synthetic, no model).
  add_executable(kv_q8_parity src/kernels/kv_q8_parity.cpp)
  target_include_directories(kv_q8_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(kv_q8_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME kv_q8_parity COMMAND kv_q8_parity)

  # KV streaming (docs/kv-streaming-design.md): streamed vs resident attention, bitwise (GPU, synthetic, no model).
  add_executable(kv_stream_parity src/kernels/kv_stream_parity.cpp)
  target_include_directories(kv_stream_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(kv_stream_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME kv_stream_parity COMMAND kv_stream_parity)

  # #2403: the verify window's KV calls on a streamed layer and on a ring (steps appends with the host copy, batched
  # resolve + attention, plain launches and graph replays), q4_0 / int8 / K8V4 (GPU, synthetic, no model).
  add_executable(kv_window_parity src/kernels/kv_window_parity.cpp)
  target_include_directories(kv_window_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(kv_window_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME kv_window_parity COMMAND kv_window_parity)

  # PR #120: the hybrid K8V4 KV (INT8 K + rotated Q4_0 V) vs host references (GPU, synthetic, no model).
  add_executable(kv_hybrid_parity src/kernels/kv_hybrid_parity.cpp)
  target_include_directories(kv_hybrid_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(kv_hybrid_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME kv_hybrid_parity COMMAND kv_hybrid_parity)
  # PR #21: Q4_0 KV with Walsh-Hadamard rotation vs a host reference (GPU, synthetic, no model).
  add_executable(kv_q4_parity src/kernels/kv_q4_parity.cpp)
  target_include_directories(kv_q4_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(kv_q4_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME kv_q4_parity COMMAND kv_q4_parity)
  # the verify window's one-launch forms (hyper-connection write, PLE post-ops with the commit's
  # history snapshots) vs the per-token launches they replace, bitwise (GPU, synthetic, no model)
  add_executable(verify_batch_parity src/kernels/verify_batch_parity.cpp)
  target_include_directories(verify_batch_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(verify_batch_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME verify_batch_parity COMMAND verify_batch_parity)
  # #2234: the batch window's one-launch-for-all-rows kernels vs the per-row launches, bitwise (GPU, synthetic)
  add_executable(batch_rows_parity src/kernels/batch_rows_parity.cpp)
  target_include_directories(batch_rows_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(batch_rows_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME batch_rows_parity COMMAND batch_rows_parity)
  add_executable(cvec_parity src/kernels/cvec_parity.cpp)
  target_include_directories(cvec_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(cvec_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME cvec_parity COMMAND cvec_parity)

  # The rewritten per-hit and grouped expert kernels vs the previous ones, bitwise (GPU, synthetic, no model).
  # `--bench` adds previous/new timings on engine-like shapes.
  add_executable(s2_expert_grouped_parity src/kernels/s2_expert_grouped_parity.cpp)
  target_include_directories(s2_expert_grouped_parity PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(s2_expert_grouped_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME s2_expert_grouped_parity COMMAND s2_expert_grouped_parity --selftest)

  # Plan v0.3 P3: native MMVQ for 1..8 tokens (bitwise selftest + --bench timing); synthetic data, no model.
  if(STRATA_BUILD_TESTS AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/bench/micro/native_mmvq_multi.cpp")
    add_executable(native_mmvq_multi bench/micro/native_mmvq_multi.cpp)
    target_include_directories(native_mmvq_multi PRIVATE ${_strata_gpu_include_directories})
    target_link_libraries(native_mmvq_multi PRIVATE strata_kernels ${_strata_gpu_runtime_target})
    add_test(NAME native_mmvq_multi COMMAND native_mmvq_multi --selftest)
  endif()

  # Plan v0.3 P0.1: working-set lock of a host region (CPU only).
  add_executable(platform_memory_test src/platform/memory_test.cpp)
  target_link_libraries(platform_memory_test PRIVATE strata_core ${_strata_gpu_runtime_target})
  add_test(NAME platform_memory_test COMMAND platform_memory_test)
  set_tests_properties(platform_memory_test PROPERTIES SKIP_RETURN_CODE 77)

  # The pinned-memory headroom (pin_budget.hpp) against a simulated ceiling; skips without a GPU.
  add_executable(pin_budget_test src/core/pin_budget_test.cpp)
  target_link_libraries(pin_budget_test PRIVATE strata_core ${_strata_gpu_runtime_target})
  add_test(NAME pin_budget_test COMMAND pin_budget_test)

  if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    add_executable(pinned_shared_test src/core/pinned_shared_test.cpp)
    target_link_libraries(pinned_shared_test PRIVATE strata_core ${_strata_gpu_runtime_target})
    add_test(NAME pinned_shared_test COMMAND pinned_shared_test)
  endif()

  # Plan v0.3 P2: the n-gram table read straight from the SSD. The selftest needs only the CPU and a temp file.
  add_executable(ple_reader_test src/ngram/ple_reader_test.cpp)
  target_include_directories(ple_reader_test PRIVATE ${_strata_gpu_include_directories})
  target_link_libraries(ple_reader_test PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  add_test(NAME ple_reader_selftest COMMAND ple_reader_test --selftest)

  # The GPU test runs ON THE TARGET PC. `--selftest` allocates
  # and writes real VRAM, so a machine without a device fails this test rather than skipping it silently -
  # which is correct: an engine that targets one GPU should not report a pass for having no GPU.
  add_test(NAME cuda_device_selftest COMMAND strata-device --selftest)

  # ---- P2.S5: the GraphRegistry --------------------------------------------------
  # A .cu because the test needs real kernels to capture; it links `strata_core` and not `strata_kernels`,
  # because what it exercises is the capture machinery and not any kernel in it.
  if(STRATA_BUILD_TESTS AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/bench/micro/graph_registry_test.cu")
    add_executable(graph_registry_test bench/micro/graph_registry_test.cu)
    target_include_directories(graph_registry_test PRIVATE ${_strata_gpu_include_directories})
    target_link_libraries(graph_registry_test PRIVATE strata_core ${_strata_gpu_runtime_target})
    add_test(NAME graph_registry_test COMMAND graph_registry_test)
  endif()

  # ---- P2.S5's GATE: is a device<->pinned-memory handoff graph-capturable? --------------------
  # The doorbell protocol puts the D2H of `x_f` and the miss list INSIDE G[l] - that is the whole overlap.  The
  # phase doc says to verify this "before building on it", and this project has already lost two rounds to
  # building first (a memory-only spin that DEADLOCKS on Windows, and a `cudaEventRecord` inside a capture that
  # is SILENTLY DROPPED).  It links only cudart: it is about the capture machinery, not about any kernel.
  if(STRATA_ENABLE_CUDA AND STRATA_BUILD_TESTS AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/bench/micro/pinned_capture.cu")
    add_executable(pinned_capture bench/micro/pinned_capture.cu)
    target_include_directories(pinned_capture PRIVATE ${_strata_gpu_include_directories})
    target_link_libraries(pinned_capture PRIVATE ${_strata_gpu_runtime_target})
    add_test(NAME pinned_capture COMMAND pinned_capture)
  endif()

  # Tells tests/CMakeLists.txt to add the CUDA-dependent Catch2 target.  It cannot be declared there
  # unconditionally: tests/ is added at the end of this file, but the flag has to be known before it runs.
  if(STRATA_ENABLE_CUDA)
    set(STRATA_CUDA_TESTS ON)
  endif()
endif()

# ---- strata_kernels_cpu (P2.S3) ----
#
# A separate library from `strata_kernels`; ISA flags are set on individual sources rather
# than on the target so that scalar and test code remain runnable on narrower CPUs.
#
#   * the CPU expert kernel uses `_mm256_dpbusd_epi32` (AVX512-VNNI) and `_mm256_multishift_epi64_epi8`
#     (AVX512-VBMI), so its source needs the complete feature set;
#   * a TU compiled WITH `/arch:AVX512` may use AVX-512 in ANY of its code, so the scalar fallback and the
#     test drivers must NOT be in it - otherwise the fallback is not a fallback, it is the same bet with
#     worse opt-out.  `cpu_require_expert_support()` therefore refuses at startup on a CPU without the
#     features, and the fallback exists for tests, exactly as P2.S3 specifies.
# Plan v0.3 P6: speculation components that need no GPU.
add_library(strata_spec STATIC src/spec/suffix_drafter.cpp src/spec/controller.cpp src/spec/draft_policy.cpp
            src/spec/draft_source.cpp)
target_include_directories(strata_spec PUBLIC include)
if(STRATA_BUILD_TESTS)
  add_executable(adapt_async_default_test src/core/adapt_async_default_test.cpp)
  target_include_directories(adapt_async_default_test PRIVATE include)
  add_test(NAME adapt_async_default_test COMMAND adapt_async_default_test)
  add_executable(uma_cache_test src/core/uma_cache_test.cpp)
  target_include_directories(uma_cache_test PRIVATE include)
  add_test(NAME uma_cache_test COMMAND uma_cache_test)
  add_executable(suffix_drafter_test src/spec/suffix_drafter_test.cpp)
  target_link_libraries(suffix_drafter_test PRIVATE strata_spec)
  add_test(NAME suffix_drafter_test COMMAND suffix_drafter_test)
  add_executable(controller_test src/spec/controller_test.cpp)
  target_link_libraries(controller_test PRIVATE strata_spec)
  add_test(NAME controller_test COMMAND controller_test)
  add_executable(draft_policy_test src/spec/draft_policy_test.cpp)
  target_link_libraries(draft_policy_test PRIVATE strata_spec)
  add_test(NAME draft_policy_test COMMAND draft_policy_test)
  add_executable(spec_start_test src/spec/spec_start_test.cpp)
  target_include_directories(spec_start_test PRIVATE include)
  add_test(NAME spec_start_test COMMAND spec_start_test)
endif()
if(STRATA_BUILD_TESTS OR STRATA_BUILD_CONVERSATION_TESTS)
  add_executable(message_boundary_test src/program/message_boundary_test.cpp)
  target_include_directories(message_boundary_test PRIVATE include)
  add_test(NAME message_boundary_test COMMAND message_boundary_test)
  # a layer split's ring override and the lend slots sized with it: the RAM copy must keep every borrowed slot (CPU)
  add_executable(split_ring_test src/program/split_ring_test.cpp)
  target_include_directories(split_ring_test PRIVATE include)
  add_test(NAME split_ring_test COMMAND split_ring_test)
  # the serve loop's conversation-cache retention policy (header-only, no library to link)
  add_executable(conv_cache_test src/program/conv_cache_test.cpp)
  target_include_directories(conv_cache_test PRIVATE include)
  add_test(NAME conv_cache_test COMMAND conv_cache_test)
  # which path computes the draft layer's prompt K/V, and STRATA_SPLIT_MTP_BATCH's parsing (header-only, CPU)
  add_executable(draft_kv_plan_test src/program/draft_kv_plan_test.cpp)
  target_include_directories(draft_kv_plan_test PRIVATE include)
  add_test(NAME draft_kv_plan_test COMMAND draft_kv_plan_test)
  # coupled draft sampling's counter and penalty-ring arithmetic (header-only, CPU)
  add_executable(coupled_draft_test src/core/coupled_draft_test.cpp)
  target_include_directories(coupled_draft_test PRIVATE include)
  add_test(NAME coupled_draft_test COMMAND coupled_draft_test)
  # probabilistic draft acceptance (STRATA_SPEC_PROB): rejection sampling preserves p - chi-square over 2M trials per case (CPU)
  add_executable(spec_prob_test src/core/spec_prob_test.cpp)
  target_include_directories(spec_prob_test PRIVATE include)
  add_test(NAME spec_prob_test COMMAND spec_prob_test)
  # the per-card window cap of --draft dflash2 (header-only, CPU)
  add_executable(draft_cap_test src/core/draft_cap_test.cpp)
  target_include_directories(draft_cap_test PRIVATE include)
  add_test(NAME draft_cap_test COMMAND draft_cap_test)
  # the session's one shared RoPE table: `session_bytes(..., borrow_rope)` is smaller by exactly one
  # max_cells*(n_rot/2)*4*2 table for every range / cell count / k, and the table is a 256-byte multiple so no
  # alignment slack can change it.
  add_executable(session_rope_share_test tests/core/session_rope_share_test.cpp)
  target_include_directories(session_rope_share_test PRIVATE include)
  if(TARGET strata_engine)
    target_link_libraries(session_rope_share_test PRIVATE strata_engine)
    target_compile_definitions(session_rope_share_test PRIVATE STRATA_TEST_REAL_ENGINE=1)
  endif()
  add_test(NAME session_rope_share_test COMMAND session_rope_share_test)
  set_tests_properties(session_rope_share_test PROPERTIES ENVIRONMENT "CUDA_VISIBLE_DEVICES=-1" TIMEOUT 60)
  # the MTP draft logits' width (max_t rows over the draft head's token subset or the whole vocabulary), the
  # zero increment bind_bytes() returns once head_logits is wide enough, and the card-class / Turing constants
  # (CPU only; mtp.cpp is in strata_engine, so the real function is used when the engine exists)
  add_executable(mtp_logits_width_test tests/core/mtp_logits_width_test.cpp)
  target_include_directories(mtp_logits_width_test PRIVATE include)
  if(TARGET strata_engine)
    target_link_libraries(mtp_logits_width_test PRIVATE strata_engine)
    target_compile_definitions(mtp_logits_width_test PRIVATE STRATA_TEST_REAL_ENGINE=1)
  endif()
  add_test(NAME mtp_logits_width_test COMMAND mtp_logits_width_test)
  set_tests_properties(mtp_logits_width_test PROPERTIES ENVIRONMENT "CUDA_VISIBLE_DEVICES=-1" TIMEOUT 60)
  # the f32 -> bf16 rounding against ggml's, NaN included, over every f32 bit pattern (header-only, CPU)
  add_executable(bf16_bits_test src/kernels/bf16_bits_test.cpp)
  target_include_directories(bf16_bits_test PRIVATE include)
  add_test(NAME bf16_bits_test COMMAND bf16_bits_test)
  # the batch window's row layout (a slot's group of proposed rows, the layer split's hand-off capacity; header-only, CPU)
  add_executable(batch_rows_test tests/core/batch_rows_test.cpp)
  target_include_directories(batch_rows_test PRIVATE include)
  add_test(NAME batch_rows_test COMMAND batch_rows_test)
  # how many pipelined slot groups --batch runs with: the 0.1.41 default, and --batch-mtp keeping it serial (header-only, CPU)
  add_executable(batch_groups_test src/program/batch_groups_test.cpp)
  target_include_directories(batch_groups_test PRIVATE include)
  add_test(NAME batch_groups_test COMMAND batch_groups_test)
endif()

add_library(strata_kernels_cpu STATIC src/kernels/cpu/expert.cpp src/kernels/cpu/pool.cpp
            src/kernels/cpu/expert_layout.cpp src/kernels/cpu/q2_avx2.cpp)
target_include_directories(strata_kernels_cpu PUBLIC include)
# Plan v0.3 P6: only the kernel files get the AVX-512 / AVX2 flags; the pool and the probe run on any x86-64,
# and the engine picks the kernels at run time (cpu_avx512_ok).
# GCC/Clang spell it differently, and `-mavx512vnni` alone is not enough: VBMI is a separate feature bit.
set_source_files_properties(src/kernels/cpu/expert.cpp
                            PROPERTIES COMPILE_OPTIONS "-mavx512f;-mavx512bw;-mavx512vl;-mavx512dq;-mavx512vnni;-mavx512vbmi;-mf16c;-mfma")
set_source_files_properties(src/kernels/cpu/q2_avx2.cpp PROPERTIES COMPILE_OPTIONS "-mavx2;-mfma;-mf16c")
# AVX-VNNI (Alder Lake, Sapphire Rapids and later): the AVX-2 expert kernels' vpdpwssd / vpdpbusd copies (q2_avx2.cpp,
# iq_avx2.cpp; *_rows.inl), taken at run time where cpu_avxvnni_ok().  GCC and Clang compile only those functions with
# target("avxvnni") rather than enabling the feature for a whole source file. A compiler without
# the intrinsics (GCC < 11, Clang < 12) builds the files without the copies.
include(CheckCXXSourceCompiles)
set(CMAKE_REQUIRED_FLAGS "-mavx2 -mfma -mf16c")
check_cxx_source_compiles([=[
#include <immintrin.h>
#if defined(__GNUC__) || defined(__clang__)
__attribute__((target("avxvnni")))
#endif
static __m256i dots(__m256i a, __m256i b) { return _mm256_dpbusd_avx_epi32(_mm256_dpwssd_avx_epi32(a, a, b), a, b); }
int main() {
    volatile int x = 1;
    return _mm256_extract_epi32(dots(_mm256_set1_epi32(x), _mm256_set1_epi32(x)), 0) == 0;
}
]=] STRATA_HAVE_AVXVNNI_INTRINSICS)
unset(CMAKE_REQUIRED_FLAGS)
if(STRATA_HAVE_AVXVNNI_INTRINSICS)
  set(_strata_avxvnni STRATA_AVXVNNI=1)
else()
  set(_strata_avxvnni STRATA_AVXVNNI=0)
endif()
set_property(SOURCE src/kernels/cpu/q2_avx2.cpp APPEND PROPERTY COMPILE_DEFINITIONS ${_strata_avxvnni})
find_package(Threads REQUIRED)
target_link_libraries(strata_kernels_cpu PUBLIC Threads::Threads)

# ---- the production CLI/server's CPU experts (IQ3_XXS gate/up, IQ4_NL or Q2_0 down; Q8_0 fallback) ----
#
# The CPU computes a native expert with ggml-cpu's own dot products (`ggml_get_type_traits_cpu`), so an IQ
# weight means exactly what it means in llama.cpp.  ggml is built static from a llama.cpp checkout: a local one
# with -DSTRATA_GGML_DIR=..., otherwise the pinned upstream commit (third_party/ggml/VERSION.txt).
set(STRATA_GGML_DIR "" CACHE PATH "llama.cpp checkout to take ggml from (empty: fetch the pinned commit)")
if(STRATA_NATIVE_EXPERTS)
  enable_language(C)
  set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
  set(GGML_STATIC ON CACHE BOOL "" FORCE)
  set(GGML_CUDA OFF CACHE BOOL "" FORCE)
  set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
  set(GGML_BACKEND_DL OFF CACHE BOOL "" FORCE)
  set(GGML_BUILD_TESTS OFF CACHE BOOL "" FORCE)
  set(GGML_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
  if(STRATA_PORTABLE)
    set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
    foreach(_f GGML_AVX GGML_AVX2 GGML_FMA GGML_F16C GGML_BMI2)
      set(${_f} ON CACHE BOOL "" FORCE)
    endforeach()
  elseif(STRATA_ISA_FLOOR)
    # the experimental older-CPU build (STRATA_ISA_FLOOR above): one ggml-cpu for the floor, ggml's "sandybridge"
    # feature set (SSE4.2 + AVX) or SSE4.2 alone - not the build host's, which may be newer than the PC it runs on.
    # From the Strata_Dirigo fork (rwkeyes).  ggml's defaults turn the rest on when GGML_NATIVE is off: all off here.
    set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
    set(GGML_CPU_ALL_VARIANTS OFF CACHE BOOL "" FORCE)
    set(GGML_SSE42 ON CACHE BOOL "" FORCE)
    if(STRATA_ISA_FLOOR STREQUAL "avx")
      set(GGML_AVX ON CACHE BOOL "" FORCE)
    else()
      set(GGML_AVX OFF CACHE BOOL "" FORCE)
    endif()
    foreach(_f GGML_AVX2 GGML_FMA GGML_F16C GGML_BMI2 GGML_AVX_VNNI GGML_AVX512 GGML_AVX512_VBMI GGML_AVX512_VNNI
               GGML_AVX512_BF16 GGML_AMX_TILE GGML_AMX_INT8 GGML_AMX_BF16)
      set(${_f} OFF CACHE BOOL "" FORCE)
    endforeach()
  else()
    set(GGML_NATIVE ON CACHE BOOL "" FORCE)
  endif()
  if(STRATA_GGML_DIR)
    add_subdirectory(${STRATA_GGML_DIR}/ggml ${CMAKE_BINARY_DIR}/ggml EXCLUDE_FROM_ALL)
  else()
    include(FetchContent)
    FetchContent_Declare(strata_llamacpp
      GIT_REPOSITORY https://github.com/ggml-org/llama.cpp.git
      GIT_TAG 3cf03257f219afbe7334045ff7c6a06ac68c627d
      GIT_SHALLOW FALSE)
    FetchContent_GetProperties(strata_llamacpp)
    if(NOT strata_llamacpp_POPULATED)
      FetchContent_Populate(strata_llamacpp)
    endif()
    add_subdirectory(${strata_llamacpp_SOURCE_DIR}/ggml ${CMAKE_BINARY_DIR}/ggml EXCLUDE_FROM_ALL)
  endif()
  # part of strata_kernels_cpu (the pool calls these and they call its CPU probe: one library, so GNU ld's
  # one-pass static linking has no cycle to resolve)
  target_sources(strata_kernels_cpu PRIVATE src/kernels/cpu/native_expert.cpp src/kernels/cpu/iq_avx512.cpp
                                           src/kernels/cpu/iq_avx2.cpp src/kernels/cpu/router_avx2.cpp
                                           src/kernels/cpu/router_avx1.cpp src/kernels/cpu/q2_avx1.cpp
                                           src/kernels/cpu/iq_avx1.cpp)
  target_include_directories(strata_kernels_cpu PRIVATE third_party/ggml)
  set_source_files_properties(src/kernels/cpu/iq_avx512.cpp PROPERTIES COMPILE_OPTIONS
                              "-mavx512f;-mavx512bw;-mavx512vl;-mavx512dq;-mavx512vnni;-mavx512vbmi;-mavx512vpopcntdq;-mbmi2;-mf16c;-mfma")
  set_source_files_properties(src/kernels/cpu/iq_avx2.cpp PROPERTIES COMPILE_OPTIONS "-mavx2;-mfma;-mf16c")
  set_source_files_properties(src/kernels/cpu/router_avx2.cpp PROPERTIES COMPILE_OPTIONS "-mavx2;-mfma;-mf16c")
  set_source_files_properties(src/kernels/cpu/router_avx1.cpp PROPERTIES COMPILE_OPTIONS "-mavx")
  # the older CPUs' Q2_0 rows and activation quantizer (q2_avx1.cpp, issue #1699): SSE4.2 + AVX with
  # AVX2/FMA/F16C explicitly off, so a -march=native build host cannot leak AVX2 code into the file that
  # runs on a Sandy Bridge Xeon
  set_source_files_properties(src/kernels/cpu/q2_avx1.cpp PROPERTIES COMPILE_OPTIONS
                              "-msse4.2;-mavx;-mno-avx2;-mno-fma;-mno-f16c")
  # the older CPUs' i-quant expert rows (iq_avx1.hpp): SSE4.2 + AVX with AVX2/FMA/F16C explicitly off, so a
  # -march=native build host cannot leak AVX2 code into the file that runs on a Sandy Bridge Xeon
  set_source_files_properties(src/kernels/cpu/iq_avx1.cpp PROPERTIES COMPILE_OPTIONS
                              "-msse4.2;-mavx;-mno-avx2;-mno-fma;-mno-f16c")
  set_property(SOURCE src/kernels/cpu/iq_avx2.cpp APPEND PROPERTY COMPILE_DEFINITIONS ${_strata_avxvnni})
  if(STRATA_ISA_FLOOR STREQUAL "avx")
    target_compile_definitions(strata_kernels_cpu PRIVATE STRATA_ISA_FLOOR_AVX=1)
  elseif(STRATA_ISA_FLOOR STREQUAL "none")
    target_compile_definitions(strata_kernels_cpu PRIVATE STRATA_ISA_FLOOR_NONE=1)
  endif()
  target_link_libraries(strata_kernels_cpu PUBLIC ggml-cpu ggml-base)
  target_compile_definitions(strata_kernels_cpu PUBLIC STRATA_NATIVE_EXPERTS=1)
  if(STRATA_BUILD_TESTS)
    # the Q5_1 block dequantizer (a Q5_1 PLE table) against ggml's, synthetic blocks: no model, no GPU
    add_executable(dequant_q5_1_test tests/core/dequant_q5_1_test.cpp)
    target_link_libraries(dequant_q5_1_test PRIVATE strata_artifact ggml-base)
    add_test(NAME dequant_q5_1_test COMMAND dequant_q5_1_test)
  endif()
  # the router lookahead's dot on each rung this CPU has (AVX2, the older CPUs' AVX1) against a double reference
  add_executable(router_dot_parity src/kernels/cpu/router_dot_parity.cpp)
  target_link_libraries(router_dot_parity PRIVATE strata_kernels_cpu)
  if(STRATA_BUILD_TESTS)
    add_executable(pool_tasks_test src/kernels/cpu/pool_tasks_test.cpp)
    target_link_libraries(pool_tasks_test PRIVATE strata_kernels_cpu)
    add_test(NAME pool_tasks_test COMMAND pool_tasks_test)
    set_tests_properties(pool_tasks_test PROPERTIES TIMEOUT 180)
    add_test(NAME router_dot_parity COMMAND router_dot_parity)
    add_test(NAME router_dot_parity_avx COMMAND ${CMAKE_COMMAND} -E env STRATA_FORCE_ISA=avx $<TARGET_FILE:router_dot_parity>)
  endif()
  # the AVX-2 expert kernels' variants (iq_avx2.cpp, q2_avx2.cpp) bit for bit against each other at 1..8 tokens and
  # against ggml-cpu's dot, on random blocks; --bench: ms per expert on one thread, the weights streamed from DRAM
  add_executable(iq_avx2_parity src/kernels/cpu/iq_avx2_parity.cpp)
  target_link_libraries(iq_avx2_parity PRIVATE strata_kernels_cpu)
  # the 512-bit i-quant rows against ggml-cpu's dot: the exact rows bit for bit at 1-4 tokens, the engine's multi-token rows to float
  # rounding, with the grid lookups as scalar loads and as gathers (SKIP without AVX-512)
  add_executable(iq512_gather_parity src/kernels/cpu/iq512_gather_parity.cpp)
  target_link_libraries(iq512_gather_parity PRIVATE strata_kernels_cpu)
  if(STRATA_BUILD_TESTS)
    add_test(NAME iq512_gather_parity_scalar COMMAND ${CMAKE_COMMAND} -E env STRATA_IQ_GATHER=0 $<TARGET_FILE:iq512_gather_parity>)
    add_test(NAME iq512_gather_parity_gather COMMAND ${CMAKE_COMMAND} -E env STRATA_IQ_GATHER=1 $<TARGET_FILE:iq512_gather_parity>)
  endif()
  if(STRATA_BUILD_TESTS)
    add_test(NAME iq_avx2_parity COMMAND iq_avx2_parity)
  endif()
  # the older CPUs' AVX1 expert rows (iq_avx1.cpp) against ggml-cpu's dots and against their own one-token
  # rows at every width; --bench: ms per expert, the weights streamed from DRAM.  Runs on any AVX CPU:
  # on an AVX2 machine it checks the older-CPU path as an AVX-only machine would run it.
  add_executable(iq_avx1_parity src/kernels/cpu/iq_avx1_parity.cpp)
  target_link_libraries(iq_avx1_parity PRIVATE strata_kernels_cpu)
  if(STRATA_BUILD_TESTS)
    add_test(NAME iq_avx1_parity COMMAND iq_avx1_parity)
  endif()
  # the AVX-2 Q8_K activation quantizer against ggml's, byte for byte (--bench: its time)
  add_executable(q8k_quant_parity src/kernels/cpu/q8k_quant_parity.cpp)
  target_link_libraries(q8k_quant_parity PRIVATE strata_kernels_cpu)
  if(STRATA_BUILD_TESTS)
    add_test(NAME q8k_quant_parity COMMAND q8k_quant_parity)
    add_test(NAME q8k_quant_parity_avx COMMAND ${CMAKE_COMMAND} -E env STRATA_FORCE_ISA=avx $<TARGET_FILE:q8k_quant_parity>)
  endif()
  # The production Qwen3.8-Flash-Next expert formats and DFlash2's Q8_0 matrices.  Other formats stay on the generic
  # dequantize-to-FP16 prompt path instead of loading MMQ kernels that this server does not use.
  set(_strata_mmq_types q2_0 iq3_xxs iq4_nl q8_0)
  if(STRATA_ENABLE_CUDA)
    # prompt-speed plan step 2b: llama.cpp's MMQ kernels (ggml-cuda, MIT) for the prompt path's experts, compiled
    # from the same checkout as ggml above - only mmq.cuh's instances for the packs' types and the quantizer, with
    # the few host symbols of ggml-cuda.cu they use in src/prefill/ggml_cuda_host.cu
    if(STRATA_GGML_DIR)
      set(_strata_gg ${STRATA_GGML_DIR}/ggml)
    else()
      set(_strata_gg ${strata_llamacpp_SOURCE_DIR}/ggml)
    endif()
    set(_strata_mmq ${_strata_gg}/src/ggml-cuda)
    # #136: the production IQ3_XXS prompt experts on Strata's own int8 kernels
    # (moe_fused_iq.cu, opt-in STRATA_PF_FUSED=1).
    add_library(strata_mmq STATIC src/prefill/moe_mmq.cu src/prefill/moe_fused.cu src/prefill/moe_fused_iq.cu
                src/prefill/ggml_cuda_host.cu ${_strata_mmq}/quantize.cu)
    # q8_0 covers DFlash2's matrices and the output head; the rest are the Qwen native expert formats.
    foreach(_t IN LISTS _strata_mmq_types)
      target_sources(strata_mmq PRIVATE ${_strata_mmq}/template-instances/mmq-instance-${_t}.cu)
    endforeach()
    target_include_directories(strata_mmq PRIVATE ${_strata_gg}/include ${_strata_gg}/src ${_strata_mmq}
                               PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_compile_options(strata_mmq PRIVATE $<$<COMPILE_LANGUAGE:CUDA>:--extended-lambda -use_fast_math>)
    # GGML's CUDA error reporting calls cuGetErrorString from the driver API.
    target_link_libraries(strata_mmq PUBLIC ggml-base ${_strata_gpu_runtime_target})
    if(TARGET CUDA::cuda_driver)
      target_link_libraries(strata_mmq PUBLIC CUDA::cuda_driver)
    endif()
    target_link_libraries(strata_prefill PUBLIC strata_mmq)
    target_compile_definitions(strata_prefill PUBLIC STRATA_PREFILL_MMQ=1 STRATA_PREFILL_FUSED=1)
    if(STRATA_BUILD_TESTS)
      # #136: the fused int8 experts against a double-precision reference and the MMQ path
      add_executable(prefill_fused_moe_test tests/cuda/prefill_fused_moe_test.cpp)
      target_link_libraries(prefill_fused_moe_test PRIVATE strata_mmq ggml-base ${_strata_gpu_runtime_target})
      add_test(NAME prefill_fused_moe_test COMMAND prefill_fused_moe_test)
      set_tests_properties(prefill_fused_moe_test PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 300)
      # #136: the production IQ3_XXS gate/up with IQ4_NL or Q2_0 down
      add_executable(prefill_fused_iq_test tests/cuda/prefill_fused_iq_test.cpp)
      target_link_libraries(prefill_fused_iq_test PRIVATE strata_mmq ggml-base ${_strata_gpu_runtime_target})
      add_test(NAME prefill_fused_iq_test COMMAND prefill_fused_iq_test)
      set_tests_properties(prefill_fused_iq_test PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 600)
      # the MoE input quantized per token and scattered to its rows (mmq::quantize_scatter) against the per-row
      # gather: the same q8_1 bytes for every MMQ layout (--bench: both at 2K, 8K and 32K tokens)
      add_executable(prefill_mmq_scatter_test tests/cuda/prefill_mmq_scatter_test.cpp)
      target_link_libraries(prefill_mmq_scatter_test PRIVATE strata_mmq ggml-base ${_strata_gpu_runtime_target})
      add_test(NAME prefill_mmq_scatter_test COMMAND prefill_mmq_scatter_test)
      set_tests_properties(prefill_mmq_scatter_test PROPERTIES TIMEOUT 120)
    endif()
    # the routed-expert products (MMQ) timed at the prompt path's shapes (16 experts a launch)
    add_executable(mmq_expert_bench src/prefill/mmq_expert_bench.cpp)
    target_link_libraries(mmq_expert_bench PRIVATE strata_mmq ggml-base CUDA::cudart)
  endif()
  if(STRATA_ENABLE_CUDA)
    add_executable(native_expert_parity src/kernels/native_expert_parity.cpp)
    target_include_directories(native_expert_parity PRIVATE ${_strata_gpu_include_directories})
    # strata_engine: the --load-check mode runs the engine's start-up checks (NativeDense, check_experts_gguf)
    target_link_libraries(native_expert_parity PRIVATE strata_engine strata_kernels strata_kernels_cpu strata_artifact ${_strata_gpu_runtime_target})
    add_executable(native_expert_bench src/kernels/native_expert_bench.cpp)
    target_include_directories(native_expert_bench PRIVATE ${_strata_gpu_include_directories})
    target_link_libraries(native_expert_bench PRIVATE strata_kernels strata_kernels_cpu strata_artifact ${_strata_gpu_runtime_target})
    # MODEL-UPDATE: the dense Q8_0 GEMVs at the engine's shapes against the memory bandwidth (a benchmark, no test)
    add_executable(q8_gemv_bench src/kernels/q8_gemv_bench.cpp)
    target_include_directories(q8_gemv_bench PRIVATE ${_strata_gpu_include_directories})
    target_link_libraries(q8_gemv_bench PRIVATE strata_kernels ${_strata_gpu_runtime_target})
    if(STRATA_ENABLE_CUDA)
      # EXPDEC (docs/EXPERTDEC.md): the grouped experts' default kernels against STRATA_EXPDEC=1 at T = 1..4, bit for bit
      # and in microseconds, on synthetic blobs read cold from DRAM
      add_executable(expdec_bench EXCLUDE_FROM_ALL src/kernels/expdec_bench.cpp)
      target_include_directories(expdec_bench PRIVATE ${_strata_gpu_include_directories})
      target_link_libraries(expdec_bench PRIVATE strata_kernels ${_strata_gpu_runtime_target})
    endif()
    if(STRATA_BUILD_TESTS AND STRATA_ENABLE_CUDA)
      # The production expert pairs and the Q8_0 DFlash2 / dense format.
      set(_strata_parity_pairs iq3_xxs/iq4_nl iq3_xxs/q2_0 q8_0/q8_0)
      foreach(_pair ${_strata_parity_pairs})
        string(REPLACE "/" "_" _name "${_pair}")
        add_test(NAME native_expert_parity_${_name} COMMAND native_expert_parity --synthetic ${_pair})
        set_tests_properties(native_expert_parity_${_name} PROPERTIES TIMEOUT 120)
      endforeach()
      # #290: the BF16 token embedding (--embd-gguf) gathered and dequantized bit for bit
      add_test(NAME native_expert_parity_bf16_embd COMMAND native_expert_parity --bf16-embd)
    endif()
  endif()
  # #296's real-weight checks (OrcaRouter Q4_K_S files; docs/ORCA_Q4_K_S.md) - test programs, not part of a release
  if(STRATA_ENABLE_CUDA AND STRATA_BUILD_TESTS)
    add_executable(ple_q5_parity src/kernels/ple_q5_parity.cpp)
    target_link_libraries(ple_q5_parity PRIVATE strata_kernels strata_artifact ggml-base ${_strata_gpu_runtime_target})
    add_executable(q5_projection_parity src/kernels/q5_projection_parity.cpp)
    target_include_directories(q5_projection_parity PRIVATE ${_strata_gpu_include_directories})
    target_link_libraries(q5_projection_parity PRIVATE strata_kernels strata_artifact ggml-base ${_strata_gpu_runtime_target})
    # #291: the FP8 PLE table (tools/ple_fp8_pack.py) through PleTable vs the checkpoint's own values
    add_executable(ple_fp8_parity src/kernels/ple_fp8_parity.cpp)
    target_link_libraries(ple_fp8_parity PRIVATE strata_kernels ${_strata_gpu_runtime_target})
  endif()
endif()
if(STRATA_ENABLE_CUDA AND STRATA_BUILD_TESTS AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/bench/micro/hit_cpu_order_parity.cu")
  add_executable(hit_cpu_order_parity bench/micro/hit_cpu_order_parity.cu)
  target_link_libraries(hit_cpu_order_parity PRIVATE strata_kernels strata_kernels_cpu ${_strata_gpu_runtime_target})
endif()

if(STRATA_BUILD_TESTS)
  add_executable(pool_affinity_test src/kernels/cpu/pool_affinity_linux_test.cpp)
  target_link_libraries(pool_affinity_test PRIVATE strata_kernels_cpu)
  add_test(NAME pool_affinity_test COMMAND pool_affinity_test)
  # Plan v0.3 P6: one expert applied to several tokens, bitwise vs the single-token kernel (synthetic, CPU only).
  # plan v0.3 P5: the device dequantizers vs the artifact's CPU dequantizers on the real shards (not a ctest: needs
  # the 38 GB model; run it by hand or from tools/gpu_session.py)
  if(STRATA_ENABLE_CUDA)   # #93: it needs the device dequantizers and a GPU runtime
    add_executable(dequant_bf16_test src/kernels/dequant_bf16_test.cpp)
    target_link_libraries(dequant_bf16_test PRIVATE strata_kernels strata_artifact ${_strata_gpu_runtime_target})
  endif()

  add_executable(expert_multi_test src/kernels/cpu/expert_multi_test.cpp)
  target_link_libraries(expert_multi_test PRIVATE strata_kernels_cpu)
  add_test(NAME expert_multi_test COMMAND expert_multi_test)
  set_tests_properties(expert_multi_test PROPERTIES SKIP_RETURN_CODE 77)   # no AVX-512: skipped

  # PR #706: the opt-in AVX-2 Q2_0 bit-plane kernel (STRATA_Q2_BITPLANE=1) against the legacy one, bitwise alone/window
  if(STRATA_BUILD_TESTS AND CMAKE_SYSTEM_PROCESSOR MATCHES "AMD64|x86_64|x64|X86")
    add_executable(q2_bitplane_parity src/kernels/cpu/q2_bitplane_parity.cpp)
    target_link_libraries(q2_bitplane_parity PRIVATE strata_kernels_cpu)
    add_test(NAME q2_bitplane_parity COMMAND q2_bitplane_parity)
    set_tests_properties(q2_bitplane_parity PROPERTIES ENVIRONMENT "STRATA_Q2_BITPLANE=1")
  endif()


  add_executable(expert_parity src/kernels/cpu/expert_parity.cpp)
  target_link_libraries(expert_parity PRIVATE strata_kernels_cpu)
  # The pack is a REQUIRED input, not an optional one: this test compares against a real expert blob, and a
  # missing file would otherwise be reported as a pass by a test that skipped everything.
  add_test(NAME expert_parity COMMAND expert_parity --selftest
           WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})

  add_executable(pool_test src/kernels/cpu/pool_test.cpp)
  target_link_libraries(pool_test PRIVATE strata_kernels_cpu)
  add_test(NAME pool_test COMMAND pool_test --selftest WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})
  # where the workers are placed (one per physical core by default) and --pool-cores (real pools, no model)
  add_executable(pool_cores_test src/kernels/cpu/pool_cores_test.cpp)
  target_link_libraries(pool_cores_test PRIVATE strata_kernels_cpu)
  add_test(NAME pool_cores_test COMMAND pool_cores_test)
  # the default CPU expert worker count on mock CPUs (8c/16t, 16c/32t, 64c/128t, 2-socket 128c/256t, ...)
  add_executable(pool_plan_test src/kernels/cpu/pool_plan_test.cpp)
  target_include_directories(pool_plan_test PRIVATE include)
  add_test(NAME pool_plan_test COMMAND pool_plan_test)
  # 0.2.2: when the pool splits per NUMA node by itself (a fake sysfs node folder, mock cores; STRATA_NUMA_POOL=0, one node = unchanged)
  add_executable(numa_pool_test src/kernels/cpu/numa_pool_test.cpp)
  target_include_directories(numa_pool_test PRIVATE include)
  target_compile_features(numa_pool_test PRIVATE cxx_std_17)
  add_test(NAME numa_pool_test COMMAND numa_pool_test)
  # issue #29: tiny batches with sleeping workers, as on a big-VRAM card; a hang fails it
  add_executable(pool_stress src/kernels/cpu/pool_stress.cpp)
  target_link_libraries(pool_stress PRIVATE strata_kernels_cpu)
  add_test(NAME pool_stress COMMAND pool_stress 20)

  # #795: nothing an AVX2-only CPU can reach may hold AVX-512 / AVX-VNNI-class instructions.  (1) the linked test binary
  # (every CPU kernel file plus ggml-cpu; on Linux, the kernel library) is disassembled and any function with such instructions must be a kernel of
  # a wide-ISA file, not a weak / COMDAT copy and not a probe or helper that every CPU runs; (2) with -DSTRATA_SDE=<sde>
  # (Intel SDE) the CPU tests run on an emulated AVX2-only Haswell and on an Alder Lake (AVX-VNNI, no AVX-512): SDE stops
  # at the first instruction the chip does not have.
  if(TARGET iq_avx2_parity)
    find_package(Python3 QUIET COMPONENTS Interpreter)
    if(Python3_Interpreter_FOUND)
      # the library, not the binary: a Linux source build compiles ggml-cpu for the build machine on purpose
      add_test(NAME isa_guard_check COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/check_isa_guard.py
               $<TARGET_FILE:strata_kernels_cpu>)
      set_tests_properties(isa_guard_check PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 600)
    endif()
  endif()
  set(STRATA_SDE "" CACHE FILEPATH "Intel SDE (sde / sde64): also run the CPU tests on an emulated AVX2-only and an AVX-VNNI-only CPU")
  if(STRATA_SDE AND TARGET iq_avx2_parity)
    foreach(_chip hsw adl)
      foreach(_t iq_avx2_parity q8k_quant_parity router_dot_parity pool_tasks_test q2_bitplane_parity)
        if(TARGET ${_t})
          add_test(NAME sde_${_chip}_${_t} COMMAND ${STRATA_SDE} -${_chip} -- $<TARGET_FILE:${_t}>)
          set_tests_properties(sde_${_chip}_${_t} PROPERTIES TIMEOUT 900)
        endif()
      endforeach()
      if(TARGET q2_bitplane_parity)
        set_tests_properties(sde_${_chip}_q2_bitplane_parity PROPERTIES ENVIRONMENT "STRATA_Q2_BITPLANE=1")
      endif()
    endforeach()
  endif()

  if(UNIX)
    add_executable(direct_file_async_test tests/platform/direct_file_async.cpp src/platform/direct_file.cpp)
    target_include_directories(direct_file_async_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_link_libraries(direct_file_async_test PRIVATE Threads::Threads)
    add_test(NAME direct_file_async_test COMMAND direct_file_async_test)
    add_executable(ple_reader_worker_failure_test tests/platform/ple_reader_worker_failure.cpp
                   src/ngram/ple_reader.cpp src/platform/direct_file.cpp)
    target_include_directories(ple_reader_worker_failure_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_link_libraries(ple_reader_worker_failure_test PRIVATE Threads::Threads)
    add_test(NAME ple_reader_worker_failure_test COMMAND ple_reader_worker_failure_test)
    set_tests_properties(ple_reader_worker_failure_test PROPERTIES TIMEOUT 30)
    add_executable(ple_reader_stats_snapshot_test tests/platform/ple_reader_stats_snapshot.cpp
                   src/ngram/ple_reader.cpp src/platform/direct_file.cpp)
    target_include_directories(ple_reader_stats_snapshot_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_link_libraries(ple_reader_stats_snapshot_test PRIVATE Threads::Threads)
    add_test(NAME ple_reader_stats_snapshot_test COMMAND ple_reader_stats_snapshot_test)
    set_tests_properties(ple_reader_stats_snapshot_test PROPERTIES TIMEOUT 30)
    add_executable(progress_wait_test tests/platform/progress_wait_test.cpp)
    target_include_directories(progress_wait_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_link_libraries(progress_wait_test PRIVATE Threads::Threads)
    add_test(NAME progress_wait_test COMMAND progress_wait_test)
    set_tests_properties(progress_wait_test PROPERTIES TIMEOUT 30)
    add_executable(worker_guard_test tests/platform/worker_guard_test.cpp)
    target_include_directories(worker_guard_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_link_libraries(worker_guard_test PRIVATE Threads::Threads)
    add_test(NAME worker_guard_test COMMAND worker_guard_test)
    set_tests_properties(worker_guard_test PROPERTIES TIMEOUT 30)
    add_executable(diagnostic_thread_failure_test tests/platform/diagnostic_thread_failure.cpp)
    target_include_directories(diagnostic_thread_failure_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_link_libraries(diagnostic_thread_failure_test PRIVATE Threads::Threads)
    add_test(NAME diagnostic_thread_failure_test COMMAND diagnostic_thread_failure_test)
    set_tests_properties(diagnostic_thread_failure_test PROPERTIES TIMEOUT 30)
  endif()
  # --aux-cpus: the CPU-set derivation (pure) and the placement of live threads; header-only, no GPU
  add_executable(aux_cpus_test tests/platform/aux_cpus_test.cpp)
  target_include_directories(aux_cpus_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
  target_link_libraries(aux_cpus_test PRIVATE Threads::Threads)
  add_test(NAME aux_cpus_test COMMAND aux_cpus_test)
endif()

if(STRATA_BUILD_TESTS)
  enable_testing()
  # Model-level checks may be absent from source distributions.
  if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/tests/CMakeLists.txt")
    add_subdirectory(tests)
  endif()
endif()
