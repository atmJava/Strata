// include/strata/kernels/cpu/numa_pool.hpp - when the CPU expert pool splits per NUMA node (0.2.2).
//
// Pure decisions, no threads and no globals, so a test can feed them a made-up machine:
//   * numa_cpu_node_count_at(dir)  how many NUMA nodes with CPUs a sysfs node folder lists (Linux: /sys/devices/system/node;
//                                  Windows reads GetNumaHighestNodeNumber in pool.cpp; every other system: 1)
//   * pick_numa_pool(...)          whether the split is tried from the node count alone
//   * decide_numa_split(...)       the split's plan for a pool whose threads sit on given cores
//
// One node is exactly the code path before the split existed: pick_numa_pool answers `Unchanged` and the pool never
// reads anything else from here.
#pragma once

#include "strata/kernels/cpu/pool_plan.hpp"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <string>
#include <system_error>
#include <vector>

namespace strata::kernels::cpu {

/// The NUMA nodes with CPUs under a sysfs node folder (`node0`, `node1`, ... each holding a `cpulist`). A node without CPUs
/// (CXL or other memory-only nodes) is not counted: the pool splits threads, and a node with none has none to split.
/// A folder that is missing or has no node entries counts 0 (the caller treats 0 and 1 alike: no split).
inline int numa_cpu_node_count_at(const std::string& node_dir) {
    std::error_code ec;
    int nodes = 0;
    for (std::filesystem::directory_iterator it(node_dir, ec), end; !ec && it != end; it.increment(ec)) {
        const std::string name = it->path().filename().string();
        if (name.size() < 5 || name.compare(0, 4, "node") != 0) continue;
        if (!std::all_of(name.begin() + 4, name.end(), [](unsigned char c) { return std::isdigit(c) != 0; })) continue;
        std::ifstream f(it->path() / "cpulist");
        std::string list;
        std::getline(f, list);
        if (list.find_first_of("0123456789") != std::string::npos) ++nodes;
    }
    return nodes;
}

enum class NumaPoolChoice {
    Unchanged,   ///< one node: today's code path, the split is never tried
    Auto,        ///< two or more NUMA nodes: try the split; if it cannot run the pool stays as it was
};
struct NumaPoolPick {
    NumaPoolChoice choice = NumaPoolChoice::Unchanged;
};

/// `nodes` is the count of NUMA nodes with CPUs: two or more tries the split, one keeps the plain pool.
inline NumaPoolPick pick_numa_pool(int nodes) {
    NumaPoolPick pick;
    pick.choice = nodes >= 2 ? NumaPoolChoice::Auto : NumaPoolChoice::Unchanged;
    return pick;
}

struct NumaSplitDecision {
    bool on = false;
    std::string why;               ///< the reason when off
    int nodes[2] = {-1, -1};       ///< the two node ids, ascending
    std::vector<int> slice;        ///< each thread's slice (0 or 1)
    int per[2] = {0, 0};           ///< threads per slice
};

/// The real-node split of a pool whose threads sit on `thread_core` (workers first, then the host when it drains; -1 = no core).
/// `n_workers` tells a worker from the host in the messages; `pinned` is whether the pool pins its threads; `node_of(core)`
/// is the core's NUMA node or -1.
inline NumaSplitDecision decide_numa_split(const std::vector<int>& thread_core, int n_workers, bool pinned,
                                           const std::function<int(int)>& node_of) {
    NumaSplitDecision d;
    const int threads = (int) thread_core.size();
    std::vector<int> node((size_t) threads, -1);
    if (threads < 2) {
        d.why = "the pool has " + std::to_string(threads) + " thread, the split needs 2 or more";
        return d;
    }
    if (!pinned) {
        d.why = "the pool's threads are not pinned";
        return d;
    }
    for (int j = 0; j < threads; ++j) {
        const int c = thread_core[(size_t) j];
        if (c < 0) {
            d.why = j < n_workers ? "worker " + std::to_string(j) + " has no core to be pinned to" : "the host thread has no core";
            return d;
        }
        if ((node[(size_t) j] = node_of(c)) < 0) {
            d.why = "CPU " + std::to_string(c) + " has no known NUMA node";
            return d;
        }
    }
    std::vector<int> nodes = node;
    std::sort(nodes.begin(), nodes.end());
    nodes.erase(std::unique(nodes.begin(), nodes.end()), nodes.end());
    if (nodes.size() != 2) {
        d.why = "the pool's threads span " + std::to_string(nodes.size()) + " NUMA node(s), the NUMA split supports exactly 2";
        return d;
    }
    d.nodes[0] = nodes[0];
    d.nodes[1] = nodes[1];
    d.slice.assign((size_t) threads, 0);
    for (int j = 0; j < threads; ++j) {
        d.slice[(size_t) j] = node[(size_t) j] == nodes[1] ? 1 : 0;
        ++d.per[d.slice[(size_t) j]];
    }
    if (!numa_slices_balanced(d.per[0], d.per[1])) {
        d.why = "slices too unbalanced (node " + std::to_string(nodes[0]) + ": " + std::to_string(d.per[0]) + " thread" +
                (d.per[0] == 1 ? "" : "s") + ", node " + std::to_string(nodes[1]) + ": " + std::to_string(d.per[1]) + " thread" +
                (d.per[1] == 1 ? "" : "s") + ")";
        d.nodes[0] = d.nodes[1] = -1;
        d.slice.clear();
        d.per[0] = d.per[1] = 0;
        return d;
    }
    d.on = true;
    return d;
}

}  // namespace strata::kernels::cpu
