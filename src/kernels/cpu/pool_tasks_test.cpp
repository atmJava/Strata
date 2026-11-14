// CPU-only task partition parity. Optional real expert: pool_tasks_test GU_TYPE DOWN_TYPE BLOB_FILE
#include "strata/kernels/cpu/pool.hpp"
#include "strata/kernels/cpu/expert_layout.hpp"
#include "ggml.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

namespace cpu = strata::kernels::cpu;

static void put_env(const char* k, const char* v) {
    setenv(k, v, 1);
}
// a synthetic Strata-form (s2) expert: random codes, fp16 scales ~0.004-0.016 (as expert_multi_test's)
static void make_s2_blob(uint8_t* b, std::mt19937& rng) {
    for (size_t i = 0; i < cpu::O_GU_SCALES; ++i) b[i] = (uint8_t) rng();
    for (size_t i = cpu::O_GU_SCALES; i < cpu::BLOB; i += 2) {
        const uint16_t h = (uint16_t) (0x1C00 + rng() % 0x0800);
        std::memcpy(b + i, &h, 2);
    }
}
static bool same_h(const float* a, const float* b) { return std::memcmp(a, b, (size_t) cpu::H * sizeof(float)) == 0; }
static void nan_h(float* a) { std::fill(a, a + cpu::H, std::numeric_limits<float>::quiet_NaN()); }
static void unset_env(const char* k) {
    unsetenv(k);
}

int main(int argc, char** argv) {
    if (argc != 1 && argc != 4) return 2;
    // its activation quantiser is the AVX one at least (act_quant_any): a CPU without AVX (Westmere) would stop on an illegal instruction
    if (!cpu::cpu_avx1_ok()) { std::printf("pool_tasks_test: no AVX on this CPU, skipped\n"); return 0; }
    {
        // Start two workers, then fail the third. A constructor that leaves joinable std::thread members behind
        // terminates the process when construction unwinds; stale NUMA state would also leak into the next pool.
        setenv("STRATA_TEST_POOL_THREAD_FAIL_AT", "2", 1);
        bool failed = false;
        try {
            cpu::ExpertPool pool(4, false, true, cpu::PoolAffinity::All, 0);
        } catch (const std::exception&) {
            failed = true;
        }
        unsetenv("STRATA_TEST_POOL_THREAD_FAIL_AT");
        if (!failed || cpu::active_numa_split().on) {
            std::fprintf(stderr, "partial worker start did not clean up constructor state\n");
            return 1;
        }
        cpu::ExpertPool replacement(1, false);
        if (replacement.workers() != 1) return 1;
    }
    cpu::NativeFmt f;
    std::string err;
    if (!cpu::native_fmt(argc == 4 ? std::atoi(argv[1]) : GGML_TYPE_IQ3_XXS,
                         argc == 4 ? std::atoi(argv[2]) : GGML_TYPE_IQ4_NL, cpu::H, cpu::FF, f, err)) {
        std::fprintf(stderr, "%s\n", err.c_str());
        return 1;
    }
    std::vector<uint8_t> blob(f.bytes);
    if (argc == 4) {
        std::FILE* file = std::fopen(argv[3], "rb");
        if (!file) return 2;
        const size_t got = std::fread(blob.data(), 1, blob.size(), file);
        std::fclose(file);
        if (got != blob.size()) return 2;
    } else {
        std::vector<float> weights((size_t) cpu::H * cpu::FF);
        for (size_t i = 0; i < weights.size(); ++i) weights[i] = 0.02f * std::sin((float) i * 0.17f);
        ggml_quantize_chunk(GGML_TYPE_IQ3_XXS, weights.data(), blob.data(), 0, cpu::FF, cpu::H, nullptr);
        ggml_quantize_chunk(GGML_TYPE_IQ3_XXS, weights.data(), blob.data() + f.up_off, 0, cpu::FF, cpu::H, nullptr);
        ggml_quantize_chunk(GGML_TYPE_IQ4_NL, weights.data(), blob.data() + f.down_off, 0, cpu::H, cpu::FF, nullptr);
    }
    for (int tasks : {-1, cpu::ExpertPool::kMaxTasks + 1}) {
        try { cpu::ExpertPool invalid(1, false, true, cpu::PoolAffinity::All, tasks); return 1; }
        catch (const std::invalid_argument&) {}
    }
    std::vector<std::vector<uint8_t>> act(cpu::MAXT, std::vector<uint8_t>(cpu::kNativeActBytes));
    std::vector<cpu::ActQ> qact(cpu::MAXT);
    std::vector<float> x(cpu::H);
    for (int t = 0; t < cpu::MAXT; ++t) {
        for (int i = 0; i < cpu::H; ++i) x[i] = std::sin((float) (i + 13 * t) * 0.031f);
        cpu::native_quant_act(f, x.data(), act[t].data());
        cpu::act_quant_any(x.data(), cpu::H, qact[t]);
    }
    int checks = 0, split_checks = 0, mixed_checks = 0;
    for (int n : {1, 7, cpu::ExpertPool::kMaxSplitMulti + 1}) {
        std::vector<cpu::ExpertJobMulti> jobs(n);
        std::vector<float> out((size_t) n * cpu::MAXT * cpu::H);
        for (int e = 0; e < n; ++e) {
            jobs[e].blob = blob.data();
            jobs[e].nt = e % 3 == 0 ? 1 : e % 3 == 1 ? 4 : cpu::MAXT;
            for (int t = 0; t < jobs[e].nt; ++t) {
                jobs[e].nact[t] = act[t].data();
                jobs[e].act[t] = &qact[t];
                jobs[e].out[t] = out.data() + ((size_t) e * cpu::MAXT + t) * cpu::H;
            }
        }
        { // The default partition is the arithmetic reference; inactive token slots remain zero.
            cpu::ExpertPool baseline(3, false);
            baseline.run_split_multi_native(f, jobs.data(), n);
        }
        const auto ref = out;
        for (float v : ref) if (!std::isfinite(v)) return 1;
        for (bool host : {false, true}) for (int workers : {1, 3})
            for (int tasks : {0, 1, 41, 128, 192, 256, cpu::ExpertPool::kMaxTasks}) {
                cpu::ExpertPool pool(workers, false, host, cpu::PoolAffinity::All, tasks);
                for (int repeat = 0; repeat < 2; ++repeat) {
                    std::fill(out.begin(), out.end(), 0.0f);
                    for (const auto& job : jobs) for (int t = 0; t < job.nt; ++t)
                        std::fill(job.out[t], job.out[t] + cpu::H, std::numeric_limits<float>::quiet_NaN());
                    pool.run_split_multi_native(f, jobs.data(), n);
                    if (std::memcmp(ref.data(), out.data(), out.size() * sizeof(float)) != 0) {
                        std::fprintf(stderr, "mismatch n=%d workers=%d host=%d tasks=%d repeat=%d\n",
                                     n, workers, host, tasks, repeat);
                        return 1;
                    }
                    ++checks;
                }
            }
#if defined(__linux__)
    {   // 0.2.2: a made-up two-node machine (STRATA_TEST_SYSFS_ROOT): the pool's own node lookup, the automatic split, its log lines
        const std::vector<int> cores = cpu::detect_cpu_topology(true, cpu::PoolAffinity::All).worker_cores;
        const int host = cpu::detect_cpu_topology(true, cpu::PoolAffinity::All).host_core;
        if (cores.size() < 4 || host < 0) {
            std::printf("pool tasks: fake two-node machine SKIPPED (needs 4 worker cores and a host core)\n");
        } else {
            namespace fs = std::filesystem;
            const fs::path root = fs::temp_directory_path() / ("strata_pool_fake_sysfs_" + std::to_string((long long) std::time(nullptr)));
            // node k of worker core i: the workers alternate between the nodes, the host core is on node 0
            auto build = [&](int n_nodes) {
                fs::remove_all(root);
                fs::create_directories(root / "devices/system/node");
                std::vector<std::string> list((size_t) n_nodes);
                auto put = [&](int cpu, int node) {
                    fs::create_directories(root / "devices/system/cpu" / ("cpu" + std::to_string(cpu)) / ("node" + std::to_string(node)));
                    list[(size_t) node] += (list[(size_t) node].empty() ? "" : ",") + std::to_string(cpu);
                };
                put(host, 0);
                for (size_t i = 0; i < cores.size(); ++i) put(cores[i], (int) (i % (size_t) n_nodes));
                for (int k = 0; k < n_nodes; ++k) {
                    fs::create_directories(root / "devices/system/node" / ("node" + std::to_string(k)));
                    std::ofstream(root / "devices/system/node" / ("node" + std::to_string(k)) / "cpulist") << list[(size_t) k] << "\n";
                }
            };
            put_env("STRATA_TEST_SYSFS_ROOT", root.string().c_str());
            const int workers = (int) std::min<size_t>(cores.size(), 8) & ~1;   // even: the nodes get the same count

            build(2);   // two nodes, nothing set: the split runs by itself
            {
                cpu::ExpertPool pool(workers, true, true);
                const auto& plan = cpu::active_numa_split();
                const bool ok = plan.on && !plan.test && plan.nodes[0] == 0 && plan.nodes[1] == 1 && pool.numa_slices() == 2 &&
                                !plan.cores[0].empty() && !plan.cores[1].empty();
                std::printf("pool tasks: fake two nodes, nothing set: split %s (%zu + %zu cores)\n", plan.on ? "on" : "off",
                            plan.cores[0].size(), plan.cores[1].size());
                if (!ok) { std::fprintf(stderr, "the automatic split did not run on a two-node machine\n"); return 1; }
            }
            for (int nodes : {1, 3}) {   // one node: the pool before the split; three: not supported, the pool as it was
                build(nodes);
                cpu::ExpertPool pool(workers, true, true);
                if (cpu::active_numa_split().on || pool.numa_slices() != 0) { std::fprintf(stderr, "split on a %d-node machine\n", nodes); return 1; }
            }
            {   // the default worker count and cores of a one-node machine do not depend on the split code at all
                build(1);
                cpu::ExpertPool a(0, true, true);
                const std::vector<int> a_cores = a.worker_cores();
                const int a_workers = a.workers();
                unset_env("STRATA_TEST_SYSFS_ROOT");
                cpu::ExpertPool b(0, true, true);
                if (a_workers != b.workers() || a_cores != b.worker_cores()) {
                    std::fprintf(stderr, "a one-node fake machine changed the pool's workers or cores\n");
                    return 1;
                }
                std::printf("pool tasks: one node: %d workers and the same cores as with no topology override\n", a_workers);
            }
            unset_env("STRATA_TEST_SYSFS_ROOT");
            fs::remove_all(root);
        }
    }
    }
#endif
    std::printf("pool tasks: %d bitwise comparisons passed, %d of them in NUMA-split mode (%d with modes 0-4 "
                "interleaved)\n", checks + split_checks + mixed_checks, split_checks + mixed_checks, mixed_checks);
    return 0;
}
