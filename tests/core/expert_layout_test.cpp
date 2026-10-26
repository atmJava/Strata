// tests/core/expert_layout_test.cpp - native_experts.txt (v3/v4) and the expert arena filled from GGUF shards,
// without a model or a GPU.
//
//   1. A generated 48-layer production manifest (IQ3_XXS/IQ4_NL, 512 experts) parses with contiguous offsets,
//      the expected blob size and the expected total.
//   2. The v4 shard column `gate,up,down` (an empty field = the --native shard); v5, four names, two names and a
//      trailing column are refused.
//   3. check_experts_gguf + load_experts_gguf on a synthetic arena at the real dimensions 2560/640 with
//      IQ3_XXS/IQ4_NL and the Q8_0 fallback: layer 1's gate/up in another file than its down.  Every byte of the
//      arena is compared with the source tensors.  Then the refusals: an offset that is not the tensor's, a
//      truncated file, a role named in the wrong file, a type or a shape the pack does not say, a missing file.
#include "gguf_fixture.hpp"

#include "strata/core/expert_source.hpp"
#include "strata/core/pinned.hpp"
#include "strata/kernels/cpu/expert_layout.hpp"
#include "strata/kernels/cpu/native_expert.hpp"

#include <algorithm>
#include <chrono>
#include <cstring>
#include <cstdlib>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;
using strata::kernels::cpu::ExpertLayout;

namespace {
int g_fail = 0;
void check(bool ok, const std::string& what) {
    std::printf("  %-84s %s\n", what.c_str(), ok ? "ok" : "FAIL");
    if (!ok) ++g_fail;
}

struct TempDir {
    fs::path path;
    TempDir() {
        path = fs::temp_directory_path() /
               ("strata-expert-layout-test-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        fs::create_directories(path);
    }
    ~TempDir() {
        std::error_code ignored;
        fs::remove_all(path, ignored);
    }
};

void write_text(const fs::path& p, const std::string& s) { std::ofstream(p, std::ios::binary) << s; }

bool load(const fs::path& dir, int64_t n_layers, int64_t n_expert, std::string& err) {
    err.clear();
    return strata::kernels::cpu::expert_layout_load(dir.string(), n_layers, n_expert, err);
}

// ---- 1. the production format
std::string production_manifest(int64_t n_layers, int64_t n_expert, size_t blob) {
    const int64_t total = (int64_t) blob * n_expert * n_layers;
    std::string s = "# strata native experts v3: layer gu_type d_type offset blob_bytes gate_off up_off down_off "
                    "[shard] (n_expert " + std::to_string(n_expert) + ", total " + std::to_string(total) +
                    "; absolute offsets in Qwen3.8-Flash-Next.gguf, or in the named shard beside it)\n";
    for (int64_t l = 0; l < n_layers; ++l) {
        const int64_t offset = (int64_t) blob * n_expert * l;
        const int64_t gate_off = 4096 + (int64_t) blob * 3 * l;
        s += std::to_string(l) + " 18 20 " + std::to_string(offset) + " " + std::to_string(blob) + " " +
             std::to_string(gate_off) + " " + std::to_string(gate_off + (int64_t) blob) + " " +
             std::to_string(gate_off + 2 * (int64_t) blob) + " Qwen3.8-Flash-Next.gguf\n";
    }
    return s;
}

void production_pack() {
    constexpr int64_t n_layers = 48, n_expert = 512;
    constexpr int64_t n_embd = 2560, n_ff = 640;
    strata::kernels::cpu::NativeFmt f;
    std::string err;
    if (!strata::kernels::cpu::native_fmt(18, 20, n_embd, n_ff, f, err)) {
        check(false, "production IQ3_XXS/IQ4_NL native format: " + err);
        return;
    }
    TempDir d;
    write_text(d.path / "native_experts.txt", production_manifest(n_layers, n_expert, f.bytes));
    const bool ok = load(d.path, n_layers, n_expert, err);
    check(ok, "production IQ3_XXS/IQ4_NL manifest: loads" + (ok ? "" : ": " + err));
    if (!ok) return;

    const ExpertLayout& L = strata::kernels::cpu::expert_layout();
    bool fields = L.version == 3 && L.n_layers == n_layers && L.n_expert == n_expert &&
                  L.fmt.size() == (size_t) n_layers && L.offset.size() == (size_t) n_layers &&
                  L.bytes.size() == (size_t) n_layers && L.gguf_off.size() == (size_t) (3 * n_layers) &&
                  L.gguf_file.size() == (size_t) (3 * n_layers);
    for (int64_t l = 0; l < n_layers && fields; ++l) {
        const int64_t offset = (int64_t) f.bytes * n_expert * l;
        const int64_t gate_off = 4096 + (int64_t) f.bytes * 3 * l;
        fields = L.fmt[(size_t) l].gu_type == 18 && L.fmt[(size_t) l].d_type == 20 &&
                 L.fmt[(size_t) l].layer == l && L.offset[(size_t) l] == (uint64_t) offset &&
                 L.bytes[(size_t) l] == f.bytes;
        for (int role = 0; role < 3; ++role) {
            const size_t i = (size_t) (3 * l + role);
            fields = fields && L.gguf_off[i] == (uint64_t) (gate_off + role * (int64_t) f.bytes) &&
                     L.gguf_file[i] == "Qwen3.8-Flash-Next.gguf";
        }
    }
    const uint64_t total = (uint64_t) f.bytes * n_expert * n_layers;
    check(fields && L.max_blob == f.bytes && L.total == total,
          "production manifest: 48 layers x 512 experts, contiguous IQ3_XXS/IQ4_NL blobs and total " +
              std::to_string(L.total));
}

// ---- 2. the v4 column and the refusals
std::string manifest(int version, const std::string& col1, int64_t blob, int n_expert = 2) {
    std::string s = "# strata native experts v" + std::to_string(version) +
                    ": layer gu_type d_type offset blob_bytes gate_off up_off down_off [shard | gate,up,down] "
                    "(n_expert " + std::to_string(n_expert) + ", total " + std::to_string(2 * blob * n_expert) + ")\n";
    s += "0 18 20 0 " + std::to_string(blob) + " 100 200 300\n";
    s += "1 18 20 " + std::to_string(blob * n_expert) + " " + std::to_string(blob) + " 400 500 600" +
         (col1.empty() ? "" : " " + col1) + "\n";
    return s;
}

void columns() {
    strata::kernels::cpu::NativeFmt f;
    std::string err;
    if (!strata::kernels::cpu::native_fmt(18, 20, 2560, 640, f, err)) {
        check(false, "IQ3_XXS/IQ4_NL native format: " + err);
        return;
    }
    const int64_t blob = (int64_t) f.bytes;
    TempDir d;
    write_text(d.path / "native_experts.txt", manifest(4, "B.gguf,B.gguf,", blob));
    bool ok = load(d.path, 2, 512, err);
    const ExpertLayout& L = strata::kernels::cpu::expert_layout();
    check(ok && L.version == 4 && L.gguf_file.size() == 6 && L.gguf_file[0].empty() && L.gguf_file[2].empty() &&
              L.gguf_file[3] == "B.gguf" && L.gguf_file[4] == "B.gguf" && L.gguf_file[5].empty() &&
              L.gguf_off[5] == 600,
          "v4: `B.gguf,B.gguf,` = gate and up in B.gguf, down in the --native shard" + (ok ? "" : ": " + err));
    write_text(d.path / "native_experts.txt", manifest(3, "A.gguf", blob));
    ok = load(d.path, 2, 512, err);
    check(ok && L.gguf_file[3] == "A.gguf" && L.gguf_file[4] == "A.gguf" && L.gguf_file[5] == "A.gguf",
          "v3: one name covers the three roles");
    write_text(d.path / "native_experts.txt", manifest(5, "", blob));
    ok = load(d.path, 2, 512, err);
    check(!ok && err.find("v5") != std::string::npos, "v5 is refused (\"" + err.substr(0, 40) + "...\")");
    write_text(d.path / "native_experts.txt", manifest(4, "a,b,c,d", blob));
    check(!load(d.path, 2, 512, err) && err.find("gate,up,down") != std::string::npos, "four names are refused");
    write_text(d.path / "native_experts.txt", manifest(4, "a,b", blob));
    check(!load(d.path, 2, 512, err), "two names are refused");
    write_text(d.path / "native_experts.txt", manifest(4, "a extra", blob));
    check(!load(d.path, 2, 512, err), "a column after the shard column is refused");
}

// ---- 3. the arena from synthetic shards
void arena() {
    using fixture::Tensor;
    constexpr int64_t H = 2560, FF = 640, NE = 2;
    const uint32_t IQ3_XXS = 18, IQ4_NL = 20, Q2_0 = 42, Q8_0 = 8;
    strata::kernels::cpu::NativeFmt f0, f1;
    std::string err;
    if (!strata::kernels::cpu::native_fmt(IQ3_XXS, IQ4_NL, H, FF, f0, err) ||
        !strata::kernels::cpu::native_fmt(Q8_0, Q8_0, H, FF, f1, err)) {
        check(false, "IQ3_XXS/IQ4_NL and Q8_0/Q8_0 native formats: " + err);
        return;
    }
    TempDir d;
    const fs::path A = d.path / "M-00002-of-00003.gguf", B = d.path / "M-00003-of-00003.gguf";
    // A: layer 0 (all roles) and layer 1's down; B: layer 1's gate and up
    const std::vector<Tensor> ta = {{"blk.0.ffn_gate_exps.weight", {H, FF, NE}, IQ3_XXS, 11},
                                    {"blk.0.ffn_up_exps.weight", {H, FF, NE}, IQ3_XXS, 12},
                                    {"blk.0.ffn_down_exps.weight", {FF, H, NE}, IQ4_NL, 13},
                                    {"blk.1.ffn_down_exps.weight", {FF, H, NE}, Q8_0, 23}};
    const std::vector<Tensor> tb = {{"blk.1.ffn_gate_exps.weight", {H, FF, NE}, Q8_0, 21},
                                    {"blk.1.ffn_up_exps.weight", {H, FF, NE}, Q8_0, 22}};
    const auto wa = fixture::write(A, {}, ta);
    const auto wb = fixture::write(B, {}, tb);
    auto at = [](const fixture::Written& w, size_t i) { return std::to_string(w.data_start + w.offsets[i]); };
    const uint64_t total = (f0.bytes + f1.bytes) * NE;
    auto text = [&](const std::string& down1, const std::string& col1) {
        return "# strata native experts v4: layer gu_type d_type offset blob_bytes gate_off up_off down_off "
               "[shard | gate,up,down] (n_expert 2, total " + std::to_string(total) + ")\n" +
               "0 18 20 0 " + std::to_string(f0.bytes) + " " + at(wa, 0) + " " + at(wa, 1) + " " + at(wa, 2) + "\n" +
               "1 8 8 " + std::to_string(f0.bytes * NE) + " " + std::to_string(f1.bytes) + " " + at(wb, 0) + " " +
               at(wb, 1) + " " + down1 + " " + col1 + "\n";
    };
    const fs::path pack = d.path / "pack";
    fs::create_directories(pack);
    write_text(pack / "native_experts.txt", text(at(wa, 3), B.filename().string() + "," + B.filename().string() + ","));
    bool ok = load(pack, 2, 512, err);
    check(ok, "the v4 manifest of the split layer loads" + (ok ? "" : ": " + err));
    if (!ok) return;
    const ExpertLayout& L = strata::kernels::cpu::expert_layout();
    ok = strata::core::check_experts_gguf(A.string(), L, err);
    check(ok, "check_experts_gguf: every span is its tensor" + (ok ? "" : ": " + err));
    std::vector<uint8_t> dst((size_t) L.total, 0xEE);
    const strata::core::LoadStats st = strata::core::load_experts_gguf(A.string(), dst.data(), L, 3);
    check(st.ok && st.bytes == L.total && L.total == total, "load_experts_gguf reads the whole arena (" +
                                                                 std::to_string(L.total) + " B)");
    // every byte: expert e's slice of role r comes from byte e * per[r] of that role's tensor
    bool exact = true;
    for (int64_t l = 0; l < 2 && exact; ++l) {
        const auto& fm = L.fmt[(size_t) l];
        const uint64_t per[3] = {fm.up_off, fm.up_off, fm.bytes - fm.down_off};
        const uint64_t off[3] = {0, fm.up_off, fm.down_off};
        const uint8_t seed[2][3] = {{11, 12, 13}, {21, 22, 23}};
        for (int64_t e = 0; e < NE && exact; ++e)
            for (int r = 0; r < 3 && exact; ++r) {
                const uint8_t* got = dst.data() + L.blob_offset(l, e) + off[r];
                for (uint64_t j = 0; j < per[r]; ++j)
                    if (got[j] != fixture::pattern(seed[l][r], (uint64_t) e * per[r] + j)) { exact = false; break; }
            }
    }
    check(exact, "every blob is [gate | up | down] of its expert, layer 1 from both files");

    {   // CS-T: the same blobs from FileExpertSource reading the shards in place (no experts.bin in the pack)
        strata::core::FileExpertSource fs;
        fs.set_gguf(A.string());
        std::string e1;
        const bool opened = fs.open(pack.string(), 2, NE, e1);
        check(opened && fs.gguf_mode(), "FileExpertSource opens the GGUF in place" + (opened ? "" : ": " + e1));
        if (opened) {
            bool same = true, copies = true, transient = true;
            std::vector<uint8_t> buf((size_t) std::max(f0.bytes, f1.bytes));
            for (int64_t l = 0; l < 2; ++l)
                for (int64_t e = 0; e < NE; ++e) {
                    fs.begin_layer(l, nullptr, 0);
                    const uint8_t* b = fs.blob(l, e);
                    const size_t n = (size_t) L.blob_bytes(l);
                    same = same && b != nullptr && std::memcmp(b, dst.data() + L.blob_offset(l, e), n) == 0;
                    copies = copies && fs.copy_blob(l, e, buf.data()) &&
                             std::memcmp(buf.data(), dst.data() + L.blob_offset(l, e), n) == 0;
                    const uint64_t released = fs.release(l, e);
#if defined(_WIN32)
                    const char* setting = std::getenv("STRATA_FILE_RELEASE");
                    const bool enabled = setting != nullptr && std::strcmp(setting, "1") == 0;
                    check(enabled ? released > 0 : released == 0, "GGUF mapped release honors its opt-in switch");
#else
                    check(released == 0, "GGUF mapped release remains unchanged outside Windows");
#endif
                    copies = copies && fs.copy_blob(l, e, buf.data()) &&
                             std::memcmp(buf.data(), dst.data() + L.blob_offset(l, e), n) == 0;
                    transient = transient && fs.transient(l, e);
                }
            check(same && copies && transient, "blob() and copy_blob() equal the loaded arena byte for byte (transient)");
            // a blob asked again in the same layer is the same buffer; its bytes hold through two more layers
            fs.begin_layer(0, nullptr, 0);
            const uint8_t* p = fs.blob(0, 1);
            const uint8_t* q = fs.blob(0, 1);
            fs.begin_layer(1, nullptr, 0);
            (void) fs.blob(1, 0);
            (void) fs.blob(1, 1);
            fs.begin_layer(0, nullptr, 0);
            (void) fs.blob(0, 0);
            check(p == q && std::memcmp(p, dst.data() + L.blob_offset(0, 1), (size_t) L.blob_bytes(0)) == 0,
                  "an assembled blob stays put while it is in use");
            check(fs.file_read_bytes() > 0, "the file tier counts its bytes (" + std::to_string(fs.file_read_bytes()) + ")");
            // prefetch: a layer's experts fetched together on several threads, then the same bytes from blob()
            bool pre = true;
            for (int round = 0; round < 3; ++round)
                for (int64_t l = 0; l < 2; ++l) {
                    fs.begin_layer(l, nullptr, 0);
                    fs.begin_layer(l == 0 ? 1 : 0, nullptr, 0);   // age the buffers so the prefetch refills them
                    fs.begin_layer(l, nullptr, 0);
                    const int64_t both[2] = {1, 0};
                    fs.set_fetch_threads(2);
                    fs.prefetch(l, both, 2);
                    for (int64_t e = 0; e < NE; ++e) {
                        const uint8_t* b = fs.blob(l, e);
                        pre = pre && b != nullptr &&
                              std::memcmp(b, dst.data() + L.blob_offset(l, e), (size_t) L.blob_bytes(l)) == 0;
                    }
                }
            check(pre && fs.file_blob_bytes() > 0 && fs.file_ms() >= 0.0,
                  "prefetch on 2 threads, then blob(): the same bytes");
        }
        fs.close();
        // with experts.bin present, the pack's file is mapped as before, whatever set_gguf says
        {
            std::ofstream eb(pack / "experts.bin", std::ios::binary);
            eb.write((const char*) dst.data(), (std::streamsize) dst.size());
        }
        strata::core::FileExpertSource fb;
        fb.set_gguf(A.string());
        std::string e2;
        const bool ob = fb.open(pack.string(), 2, NE, e2);
        const uint8_t* b11 = ob ? fb.blob(1, 1) : nullptr;
        check(ob && !fb.gguf_mode() && !fb.transient(1, 1) && b11 != nullptr &&
                  std::memcmp(b11, dst.data() + L.blob_offset(1, 1), (size_t) L.blob_bytes(1)) == 0,
              "with experts.bin the mapped file is used (unchanged behaviour)");
        fb.close();
        fs::remove(pack / "experts.bin");
    }

    auto refused = [&](const std::string& what, const std::string& needle) {
        std::string e2;
        const bool bad = load(pack, 2, 512, e2) && !strata::core::check_experts_gguf(A.string(),
                                                                                    strata::kernels::cpu::expert_layout(), e2);
        check(bad && e2.find(needle) != std::string::npos, what + " (\"..." + needle + "...\")");
    };
    const std::string both = B.filename().string() + "," + B.filename().string() + ",";
    write_text(pack / "native_experts.txt", text(std::to_string(std::stoull(at(wa, 3)) + 32), both));
    refused("an offset that is not the tensor's is refused", "starts at byte");
    write_text(pack / "native_experts.txt", text(at(wa, 3), ""));
    refused("layer 1's gate named in the file that does not hold it is refused", "is not in it");
    write_text(pack / "native_experts.txt", text(at(wa, 3), both + ",x"));
    std::string e3;
    check(!load(pack, 2, 512, e3), "a malformed column is refused at load");
    write_text(pack / "native_experts.txt", text(at(wa, 3), "missing.gguf,missing.gguf,"));
    refused("a shard that is not there is refused", "cannot open");
    fixture::write(B, {}, tb, /*cut=*/100);
    write_text(pack / "native_experts.txt", text(at(wa, 3), both));
    refused("a truncated shard is refused before anything is read", "past the end");

    // a type and a shape the pack does not say: IQ3_XXS and Q2_0 have different block geometry.
    TempDir d2;
    const fs::path C = d2.path / "C.gguf";
    const auto wc = fixture::write(C, {}, {{"blk.0.ffn_gate_exps.weight", {H, FF, NE}, IQ3_XXS, 31},
                                           {"blk.0.ffn_up_exps.weight", {H, FF, NE}, Q2_0, 32},
                                           {"blk.0.ffn_down_exps.weight", {FF, H, NE + 1}, IQ4_NL, 33}});
    strata::kernels::cpu::NativeFmt f2;
    if (!strata::kernels::cpu::native_fmt(IQ3_XXS, IQ4_NL, H, FF, f2, err)) {
        check(false, "IQ3_XXS/IQ4_NL native format: " + err);
        return;
    }
    fs::create_directories(d2.path / "pack");
    write_text(d2.path / "pack" / "native_experts.txt",
               "# strata native experts v3: (n_expert 2, total " + std::to_string(f2.bytes * NE) + ")\n0 18 20 0 " +
                   std::to_string(f2.bytes) + " " + std::to_string(wc.data_start + wc.offsets[0]) + " " +
                   std::to_string(wc.data_start + wc.offsets[1]) + " " + std::to_string(wc.data_start + wc.offsets[2]) + "\n");
    ok = load(d2.path / "pack", 1, 512, err) && !strata::core::check_experts_gguf(C.string(),
                                                                                  strata::kernels::cpu::expert_layout(), err);
    check(ok && err.find("is Q2_0") != std::string::npos, "a gate of another type than the pack's is refused");
    // the same bytes with the pack's types, but a down tensor of three experts where the pack says two
    fixture::write(C, {}, {{"blk.0.ffn_gate_exps.weight", {H, FF, NE}, IQ3_XXS, 31},
                           {"blk.0.ffn_up_exps.weight", {H, FF, NE}, IQ3_XXS, 32},
                           {"blk.0.ffn_down_exps.weight", {FF, H, NE + 1}, IQ4_NL, 33}});
    ok = load(d2.path / "pack", 1, 512, err) && !strata::core::check_experts_gguf(C.string(),
                                                                                  strata::kernels::cpu::expert_layout(), err);
    check(ok && err.find("is not [640, 2560, 2]") != std::string::npos, "a down tensor of another shape is refused");
}
}  // namespace

int main(int argc, char** argv) {
    // `expert_layout_test --real PACK_DIR NATIVE_SHARD`: a real pack's native_experts.txt against its model's files
    // (headers only - check_experts_gguf reads no expert byte)
    if (argc == 4 && std::string(argv[1]) == "--real") {
        std::string err;
        if (!load(argv[2], 48, 512, err)) { std::printf("layout refused: %s\n", err.c_str()); return 1; }
        const ExpertLayout& L = strata::kernels::cpu::expert_layout();
        const bool ok = strata::core::check_experts_gguf(argv[3], L, err);
        size_t split = 0;
        for (int64_t l = 0; l < L.n_layers && !L.gguf_file.empty(); ++l)
            split += L.gguf_file[(size_t) (3 * l)] != L.gguf_file[(size_t) (3 * l + 2)];
        std::printf("%s: v%d, %lld layers x %lld experts, %.2f GiB, %zu layer(s) split per role: %s%s\n", argv[2],
                    L.version, (long long) L.n_layers, (long long) L.n_expert, (double) L.total / (1u << 30), split,
                    ok ? "every span is its tensor" : "REFUSED: ", ok ? "" : err.c_str());
        return ok ? 0 : 1;
    }
    std::printf("expert_layout_test\n");
    if (argc != 1) {
        std::printf("usage: expert_layout_test [--real PACK_DIR NATIVE_SHARD]\n");
        return 2;
    }
    production_pack();
    columns();
    arena();
    std::printf(g_fail ? "expert_layout_test: %d FAILED\n" : "expert_layout_test: all passed\n", g_fail);
    return g_fail ? 1 : 0;
}
