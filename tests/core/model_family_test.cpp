// tests/core/model_family_test.cpp - Qwen3.8-Flash-Next (qwen4exp) on synthetic GGUFs: no model, no GPU.
//
//   * qwen4exp is the only accepted architecture; another architecture is refused by name;
//   * geometry_from_gguf reads Flash-Next's metadata and checks it against the compiled kernels;
//   * the layout table accepts a complete tiny file and refuses a wrong shape or a missing tensor by name.
//
// `model_family_test --real SHARD`: inspect a real model (headers only): geometry, kernels and layout.
#include "gguf_fixture.hpp"

#include "strata/core/family_layout.hpp"

#include <chrono>
#include <cstdio>
#include <filesystem>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {
int g_fail = 0;
void check(bool ok, const std::string& what) {
    std::printf("  %-90s %s\n", what.c_str(), ok ? "ok" : "FAIL");
    if (!ok) ++g_fail;
}

struct TempDir {
    fs::path path;
    TempDir() {
        path = fs::temp_directory_path() /
               ("strata-model-family-test-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        fs::create_directories(path);
    }
    ~TempDir() {
        std::error_code ignored;
        fs::remove_all(path, ignored);
    }
};

using fixture::Kv;
using fixture::Tensor;
const uint32_t F32 = 0;

struct Dims {
    uint64_t block_count, embd, heads, heads_kv, key_len, experts, used, ff, ff_sh, conv, state, groups, v_heads,
        inner, interval, hc, hc_lr, idx_heads, idx_key;
};

const Dims kFlashNext = {48, 2560, 24, 2, 256, 512, 10, 640, 640, 4, 128, 16, 48, 6144, 4, 4, 320, 4, 128};
const Dims kTinyFlash = {8, 64, 2, 1, 16, 4, 2, 32, 32, 4, 8, 2, 4, 32, 4, 2, 8, 2, 8};

std::vector<Kv> metadata(const char* arch, const Dims& d, uint64_t vocab) {
    const std::string p = std::string(arch) + ".";
    std::vector<Kv> kv = {
        fixture::str("general.architecture", arch),
        fixture::u32(p + "block_count", d.block_count),
        fixture::u32(p + "embedding_length", d.embd),
        fixture::u32(p + "attention.head_count", d.heads),
        fixture::u32(p + "attention.head_count_kv", d.heads_kv),
        fixture::u32(p + "attention.key_length", d.key_len),
        fixture::u32(p + "attention.value_length", d.key_len),
        fixture::u32(p + "expert_count", d.experts),
        fixture::u32(p + "expert_used_count", d.used),
        fixture::u32(p + "expert_feed_forward_length", d.ff),
        fixture::u32(p + "expert_shared_feed_forward_length", d.ff_sh),
        fixture::u32(p + "ssm.conv_kernel", d.conv),
        fixture::u32(p + "ssm.state_size", d.state),
        fixture::u32(p + "ssm.group_count", d.groups),
        fixture::u32(p + "ssm.time_step_rank", d.v_heads),
        fixture::u32(p + "ssm.inner_size", d.inner),
        fixture::u32(p + "full_attention_interval", d.interval),
        fixture::u32(p + "hyper_connection.count", d.hc),
        fixture::u32(p + "hyper_connection.low_rank", d.hc_lr),
        fixture::u32(p + "attention.indexer.head_count", d.idx_heads),
        fixture::u32(p + "attention.indexer.key_length", d.idx_key),
    };
    std::vector<std::string> tokens;
    for (uint64_t i = 0; i < vocab; ++i) tokens.push_back("t" + std::to_string(i));
    kv.push_back(fixture::str_array("tokenizer.ggml.tokens", tokens));
    return kv;
}

std::vector<Tensor> tensors_of(const strata::core::ModelGeometry& g) {
    std::vector<Tensor> tensors;
    uint8_t seed = 1;
    auto add = [&](const std::string& name, const std::vector<int64_t>& shape) {
        std::vector<uint64_t> converted(shape.begin(), shape.end());
        tensors.push_back({name, converted, F32, seed++});
    };
    for (const auto& weight : strata::core::global_tensors(g)) add(weight.name, weight.shape);
    for (int64_t layer = 0; layer < g.n_layers; ++layer)
        for (const auto& weight : strata::core::layer_tensors(g, layer))
            add("blk." + std::to_string(layer) + "." + weight.name, weight.shape);
    return tensors;
}

bool geometry_of(const fs::path& path, strata::core::ModelGeometry& geometry, int64_t& top_k, std::string& error) {
    const strata::GgufFile file(path.string());
    return strata::core::geometry_from_gguf(file, geometry, top_k, error);
}

std::string layout_error(const fs::path& dir, const Dims& dims, uint64_t vocab,
                         void (*mutate)(std::vector<Tensor>&)) {
    strata::core::ModelGeometry geometry;
    geometry.n_layers = (int64_t) dims.block_count;
    geometry.n_embd = (int64_t) dims.embd;
    geometry.n_head = (int64_t) dims.heads;
    geometry.n_head_kv = (int64_t) dims.heads_kv;
    geometry.head_dim = (int64_t) dims.key_len;
    geometry.n_expert = (int64_t) dims.experts;
    geometry.n_ff = (int64_t) dims.ff;
    geometry.n_ff_shexp = (int64_t) dims.ff_sh;
    geometry.ssm_d_conv = (int64_t) dims.conv;
    geometry.ssm_state_size = (int64_t) dims.state;
    geometry.ssm_k_heads = (int64_t) dims.groups;
    geometry.ssm_v_heads = (int64_t) dims.v_heads;
    geometry.ssm_value_dim = (int64_t) dims.inner;
    geometry.ssm_conv_channels = 2 * geometry.ssm_state_size * geometry.ssm_k_heads + geometry.ssm_value_dim;
    geometry.hc = (int64_t) dims.hc;
    geometry.hc_lr = (int64_t) dims.hc_lr;
    geometry.idx_q_heads = (int64_t) dims.idx_heads;
    geometry.idx_key_dim = (int64_t) dims.idx_key;
    geometry.n_vocab = (int64_t) vocab;
    std::vector<Tensor> tensors = tensors_of(geometry);
    if (mutate) mutate(tensors);
    const fs::path model = dir / "tiny.gguf";
    fixture::write(model, metadata("qwen4exp", dims, vocab), tensors);
    const strata::GgufModel model_file({model.string()});
    size_t checked = 0;
    std::string error;
    if (!strata::core::check_gguf_layout(model_file, geometry, error, &checked)) return error;
    return {};
}

int real_model(const std::string& any) {
    try {
        const strata::GgufModel model = strata::GgufModel::open(any);
        const std::string architecture = strata::check_architecture(model.meta());
        std::printf("  %zu shard(s); architecture guard: %s; engine guard: %s\n", model.size(),
                    architecture.empty() ? "ok" : architecture.c_str(),
                    strata::check_engine_architecture(model.meta()).empty()
                        ? "runs"
                        : strata::check_engine_architecture(model.meta()).c_str());
        if (!architecture.empty()) return 1;
        strata::core::ModelGeometry geometry;
        int64_t top_k = 0;
        std::string error;
        if (!strata::core::geometry_from_gguf(model.meta(), geometry, top_k, error)) {
            std::printf("  geometry: %s\n", error.c_str());
            return 1;
        }
        const std::string mismatch = strata::core::compiled_geometry_mismatch(geometry);
        std::printf("  Qwen3.8-Flash-Next: %lld layers, hidden %lld, %lld experts top-%lld, vocab %lld\n",
                    (long long) geometry.n_layers, (long long) geometry.n_embd, (long long) geometry.n_expert,
                    (long long) top_k, (long long) geometry.n_vocab);
        std::printf("  compiled kernels: %s\n", mismatch.empty() ? "match" : mismatch.c_str());
        size_t checked = 0;
        const bool ok = strata::core::check_gguf_layout(model, geometry, error, &checked);
        std::printf("  layout: %s (%zu tensors checked)\n", ok ? "ok" : error.c_str(), checked);
        return ok ? 0 : 1;
    } catch (const std::exception& error) {
        std::printf("  refused: %s\n", error.what());
        return 1;
    }
}
}  // namespace

int main(int argc, char** argv) {
    if (argc == 3 && std::string(argv[1]) == "--real") return real_model(argv[2]);
    std::printf("model_family_test\n");
    TempDir directory;
    {
        const fs::path flash = directory.path / "flash.gguf";
        const fs::path other = directory.path / "other.gguf";
        fixture::write(flash, metadata("qwen4exp", kFlashNext, 248320), {});
        fixture::write(other, {fixture::str("general.architecture", "llama")}, {});
        const strata::GgufFile known(flash.string());
        const strata::GgufFile unknown(other.string());
        check(strata::model_family(known) == strata::ModelFamily::Qwen4Exp,
              "model_family: qwen4exp");
        check(strata::check_architecture(known).empty(), "the architecture guard accepts qwen4exp");
        check(strata::check_architecture(unknown).find("'llama'") != std::string::npos,
              "another architecture is refused by name");
        check(strata::check_engine_architecture(known).empty(), "the engine guard runs qwen4exp");
    }
    {
        const fs::path flash = directory.path / "flash-geometry.gguf";
        fixture::write(flash, metadata("qwen4exp", kFlashNext, 248320), {});
        strata::core::ModelGeometry geometry;
        int64_t top_k = 0;
        std::string error;
        const bool ok = geometry_of(flash, geometry, top_k, error);
        check(ok, "Flash-Next geometry reads" + (ok ? std::string() : ": " + error));
        check(geometry.n_layers == 48 && geometry.n_embd == 2560 && geometry.n_expert == 512 && top_k == 10 &&
                  geometry.n_vocab == 248320,
              "Flash-Next: 48 layers, hidden 2560, 512 experts top-10, vocab 248320");
        check(strata::core::compiled_geometry_mismatch(geometry).empty(),
              "Flash-Next metadata matches the compiled kernels");
        Dims coder = kFlashNext;
        coder.experts = 256;
        const fs::path coder_path = directory.path / "coder.gguf";
        fixture::write(coder_path, metadata("qwen4exp", coder, 8), {});
        check(geometry_of(coder_path, geometry, top_k, error) && geometry.n_expert == 256 &&
                  strata::core::compiled_geometry_mismatch(geometry).empty(),
              "the Coder's 256 experts are a run-time size, not a kernel mismatch");
    }
    {
        const std::string complete = layout_error(directory.path, kTinyFlash, 16, nullptr);
        check(complete.empty(), "a complete qwen4exp file passes its layout" +
                                    (complete.empty() ? std::string() : ": " + complete));
        std::string error = layout_error(directory.path, kTinyFlash, 16, [](std::vector<Tensor>& tensors) {
            for (auto& tensor : tensors)
                if (tensor.name == "blk.1.ssm_conv1d.weight") tensor.shape[1] += 1;
        });
        check(error.find("blk.1.ssm_conv1d.weight") != std::string::npos && error.find("wants") != std::string::npos,
              "a wrong shape is refused, naming the tensor and both shapes");
        error = layout_error(directory.path, kTinyFlash, 16, [](std::vector<Tensor>& tensors) {
            for (auto it = tensors.begin(); it != tensors.end(); ++it)
                if (it->name == "blk.3.indexer.k_proj.weight") {
                    tensors.erase(it);
                    break;
                }
        });
        check(error == "missing blk.3.indexer.k_proj.weight",
              "a missing indexer tensor is refused");
    }
    std::printf(g_fail ? "FAILED (%d)\n" : "ALL PASSED\n", g_fail);
    return g_fail ? 1 : 0;
}
