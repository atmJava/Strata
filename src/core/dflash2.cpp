// src/core/dflash2.cpp - see include/strata/core/dflash2.hpp.
#include "strata/core/dflash2.hpp"
#include "strata/core/graph_capture.hpp"
#include "strata/core/mapped_host.hpp"
#include "strata/core/scoped_value_restore.hpp"

#include "strata/core/native_head.hpp"
#include "strata/core/on_device.hpp"
#include "strata/core/weights.hpp"
#include "strata/kernels/dflash2.hpp"
#include "strata/kernels/elementwise.hpp"
#include "strata/kernels/native_mmvq.hpp"
#include "strata/kernels/native_qsa.hpp"
#include "strata/kernels/verify_kernels.hpp"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <atomic>
#include <initializer_list>
#include <stdexcept>

namespace strata::core {
namespace {

using Clock = std::chrono::steady_clock;
double ms_since(Clock::time_point t) { return std::chrono::duration<double, std::milli>(Clock::now() - t).count(); }

bool dflash_device_ok(const OnDevice& on, int device, const char* where, std::string& err) {
    if (on.ok) return true;
    err = std::string("dflash2: cannot enter CUDA") + std::to_string(device) + " for " + where + ": " +
          cudaGetErrorString(on.error);
    return false;
}

bool mapped(size_t bytes, void** h, void** d) {
    return strata::core::alloc_mapped_pinned(bytes, h, d);
}

constexpr int GGML_Q8_0 = 8;

// The pack blob, the draft vocabulary and the draft head rows are shared between a batch's slots: a shared_ptr holds each one
// so it lives until the last instance that loaded or shared it is gone.  Every release happens inside a destructor body while
// OnDevice(device_) is in scope, so the cudaFree below runs on the allocating device.
struct CudaFree {
    void operator()(void* p) const {
        if (p != nullptr) cudaFree(p);
    }
};
using CudaPtr = std::shared_ptr<void>;

}  // namespace

void Dflash2Drafter::release() {
    if (device_ < 0) return;      // already released (or never loaded): the destructor calls this
    const OnDevice on_device(device_);
    if (!on_device.ok) return;    // keep every handle for a later retry: none is valid on the caller's device
    if (cs_) cudaStreamSynchronize(cs_);
    prof_report();
    prof_free();
    for (cudaGraphExec_t& e : exec_)
        if (e) cudaGraphExecDestroy(e);
    auto fr = [](void* p) { if (p) cudaFree(p); };
    fr(d_meta_); fr(xq_); fr(xqp_); fr(xdp_); fr(xqc_); fr(xdc_); fr(rt_ctx_); fr(rt_blk_);
    for (int l = 0; l < 8; ++l) { fr(kring_[l]); fr(vring_[l]); }
    fr(fcy_); fr(hctx_); fr(kc_); fr(vc_); fr(h_); fr(xn_); fr(xc_); fr(dyn_); fr(q_); fr(qn_); fr(k_); fr(v_); fr(kblk_); fr(ao_); fr(o_);
    fr(gate_); fr(up_); fr(mlp_); fr(part_); fr(fn_); fr(logits_); fr(topval_); fr(hsel_); fr(toptok_); fr(topscratch_);
    // the shared allocations go when the last instance holding them does (a slot may be destroyed before the instance it shares with)
    blob_owner_.reset();
    dvocab_owner_.reset();
    dhead_owner_.reset();
    strata::core::host_free_guarded(h_meta_);
    strata::core::host_free_guarded(h_out_);
    if (cs_) cudaStreamDestroy(cs_);
    if (cs2_) cudaStreamDestroy(cs2_);
    if (ev_fork_) cudaEventDestroy(ev_fork_);
    if (ev_join_) cudaEventDestroy(ev_join_);
    for (cudaEvent_t e : ev_ctx_) if (e) cudaEventDestroy(e);
    if (ev_a_) cudaEventDestroy(ev_a_);
    if (ev_b_) cudaEventDestroy(ev_b_);
    // every handle is gone: null it and mark the instance released, so a second call (or the destructor) does nothing.
    // A fallback to the MTP calls this, because a drafter that failed to bind still holds the pack and its ring.
    auto z = [](auto*& p) { p = nullptr; };
    z(d_meta_); z(xq_); z(xqp_); z(xdp_); z(xqc_); z(xdc_); z(rt_ctx_); z(rt_blk_);
    for (int l = 0; l < 8; ++l) { z(kring_[l]); z(vring_[l]); }
    z(fcy_); z(hctx_); z(kc_); z(vc_); z(h_); z(xn_); z(xc_); z(dyn_); z(q_); z(qn_); z(k_); z(v_); z(kblk_); z(ao_); z(o_);
    z(gate_); z(up_); z(mlp_); z(part_); z(fn_); z(logits_); z(topval_); z(hsel_); z(toptok_); z(topscratch_);
    logits_cap_ = 0;
    z(h_meta_); z(h_out_);
    z(cs_); z(cs2_);
    z(ev_fork_); z(ev_join_); z(ev_a_); z(ev_b_);
    for (cudaEvent_t& e : ev_ctx_) e = nullptr;
    for (cudaGraphExec_t& e : exec_) e = nullptr;
    // nothing is bound or shared any more: context()/draft() report "released" instead of writing freed buffers
    bound_ = false;
    blob_ = nullptr;
    dvocab_ = nullptr;
    dhead_ = nullptr;
    dvocab_bytes_ = 0;
    dhead_bytes_ = 0;
    dvocab_host_.clear();
    embed_ = {};
    head_w_ = nullptr;
    head_src_ = nullptr;
    head_row_bytes_ = 0;
    shared_ = nullptr;
    m_meta_ = nullptr;
    hp_out_ = nullptr;
    out_prob_ = nullptr;
    out_tok_ = nullptr;
    topscratch_ = nullptr;
    graph_taps_ = nullptr;
    pending_ = false;
    vram_ = 0;
    device_ = -1;
}

Dflash2Drafter::~Dflash2Drafter() {
    release();
}

// STRATA_DF2_PROFILE=1 (measurement only): the round runs without its graph and an event follows every stage; the per-stage averages print at exit
struct Dflash2Drafter::Prof {
    std::vector<cudaEvent_t> ev;
    std::vector<std::string> name;
    std::vector<std::pair<std::string, double>> total;
    int64_t rounds = 0;
    bool enabled = true;
    ~Prof() {
        for (cudaEvent_t event : ev)
            if (event) (void) cudaEventDestroy(event);
    }
};

void Dflash2Drafter::prof_free() {
    delete prof_;
    prof_ = nullptr;
}

// STRATA_DF2_SKIP=attn,head,ctx,mlp,qkvo (measurement only): leaves those parts out of the round, so the graph's GPU time without them can be read
static bool skip_part(const char* what) {
    static const std::string list = [] { const char* e = std::getenv("STRATA_DF2_SKIP"); return std::string(e ? e : ""); }();
    return !list.empty() && list.find(what) != std::string::npos;
}

void Dflash2Drafter::stamp(const char* name) {
    if (!prof_ || !prof_->enabled) return;
    cudaEvent_t event = nullptr;
    if (cudaEventCreate(&event) != cudaSuccess || cudaEventRecord(event, cs_) != cudaSuccess) {
        if (event) (void) cudaEventDestroy(event);
        prof_->enabled = false;
        (void) cudaGetLastError();
        return;
    }
    try {
        prof_->name.emplace_back(name);
    } catch (...) {
        (void) cudaEventDestroy(event);
        prof_->enabled = false;
        return;
    }
    try {
        prof_->ev.push_back(event);
    } catch (...) {
        prof_->name.pop_back();
        (void) cudaEventDestroy(event);
        prof_->enabled = false;
    }
}

void Dflash2Drafter::prof_collect() {
    if (!prof_) return;
    if (!prof_->enabled || prof_->ev.size() < 2) {
        for (cudaEvent_t event : prof_->ev)
            if (event) (void) cudaEventDestroy(event);
        prof_->ev.clear();
        prof_->name.clear();
        return;
    }
    bool valid = cudaStreamSynchronize(cs_) == cudaSuccess;
    if (valid) {
        try {
            for (size_t i = 1; i < prof_->ev.size(); ++i) {
                float ms = 0;
                if (cudaEventElapsedTime(&ms, prof_->ev[i - 1], prof_->ev[i]) != cudaSuccess) {
                    valid = false;
                    break;
                }
                auto it = std::find_if(prof_->total.begin(), prof_->total.end(),
                                       [&](const auto& t) { return t.first == prof_->name[i]; });
                if (it == prof_->total.end())
                    prof_->total.emplace_back(prof_->name[i], ms);
                else
                    it->second += ms;
            }
        } catch (...) {
            valid = false;
        }
    }
    for (cudaEvent_t e : prof_->ev) cudaEventDestroy(e);
    prof_->ev.clear();
    prof_->name.clear();
    if (valid)
        ++prof_->rounds;
    else {
        prof_->enabled = false;
        (void) cudaGetLastError();
    }
}

void Dflash2Drafter::prof_report() const {
    if (!prof_ || prof_->rounds == 0) return;
    double sum = 0;
    for (const auto& t : prof_->total) sum += t.second;
    std::fprintf(stderr, "strata dflash2 profile (%lld rounds, ms per round, total %.3f):", (long long) prof_->rounds, sum / (double) prof_->rounds);
    for (const auto& t : prof_->total) std::fprintf(stderr, " %s %.3f", t.first.c_str(), t.second / (double) prof_->rounds);
    std::fputc(10, stderr);
}

const Dflash2Drafter::Tensor& Dflash2Drafter::find(const std::string& name) const {
    for (const Tensor& t : tensors_)
        if (t.name == name) return t;
    throw std::runtime_error("dflash2: tensor " + name + " is missing from the pack");
}

// A few bytes from three places of the pack: enough to tell a different pack of the same shape from a copy, without
// reading 0.76 GiB (an empty sample means "could not read it", and the caller then trusts the index alone).
static std::vector<uint8_t> sample_pack(const std::string& path) {
    std::vector<uint8_t> out;
    std::FILE* f = std::fopen(path.c_str(), "rb");
    if (f == nullptr) return out;
    std::fseek(f, 0, SEEK_END);
    const long size = std::ftell(f);
    if (size <= 0) { std::fclose(f); return out; }
    const long chunk = 8192;
    const long offs[3] = {0, std::max(0L, size / 2 - chunk / 2), std::max(0L, size - chunk)};
    uint8_t buf[8192];
    for (long off : offs) {
        if (std::fseek(f, off, SEEK_SET) != 0) { out.clear(); break; }
        const size_t got = std::fread(buf, 1, (size_t) std::min(chunk, size - off), f);
        out.insert(out.end(), buf, buf + got);
    }
    std::fclose(f);
    return out;
}

bool Dflash2Drafter::load(const std::string& path, int device, std::string& err, const Dflash2Drafter* shared) {
    if (shared == this) { err = "dflash2: an instance cannot share its own pack"; return false; }
    if (device_ >= 0 || blob_ != nullptr || cs_ != nullptr) {
        err = "dflash2: this drafter is already loaded";
        return false;
    }
    device_ = device;
    const OnDevice on_device(device_);
    if (!on_device.ok) {
        err = std::string("dflash2: cannot enter CUDA") + std::to_string(device) + ": " +
              cudaGetErrorString(on_device.error);
        device_ = -1;
        return false;
    }
    struct Rollback {
        Dflash2Drafter& self;
        bool commit = false;
        ~Rollback() { if (!commit) self.release(); }
    } rollback{*this};
    std::FILE* f = std::fopen(path.c_str(), "rb");
    if (f == nullptr) { err = "dflash2: cannot open " + path; return false; }
    struct Closer { std::FILE* f; ~Closer() { std::fclose(f); } } closer{f};
    uint32_t hdr[3];
    if (std::fread(hdr, 4, 3, f) != 3 || std::memcmp(hdr, "DF2P", 4) != 0 || hdr[1] != 1) { err = "dflash2: " + path + " is not a DF2P v1 pack"; return false; }
    std::string index(hdr[2], ' ');
    if (std::fread(index.data(), 1, hdr[2], f) != hdr[2]) { err = "dflash2: the pack's index is cut short"; return false; }
    const long data_off = 12 + (long) hdr[2];
    uint64_t total = 0;
    for (size_t i = 0; i < index.size();) {
        const size_t j = index.find('\n', i);
        if (j == std::string::npos) break;
        const std::string line = index.substr(i, j - i);
        i = j + 1;
        char a[256], b[64], c[256];
        long long v1, v2, v3, v4;
        if (std::sscanf(line.c_str(), "cfg %63s %255s", b, c) == 2) {
            const std::string key = b;
            if (key == "hidden") N_ = std::atoi(c);
            else if (key == "layers") L_ = std::atoi(c);
            else if (key == "heads") NH_ = std::atoi(c);
            else if (key == "kv_heads") NKV_ = std::atoi(c);
            else if (key == "head_dim") HD_ = std::atoi(c);
            else if (key == "inter") I_ = std::atoi(c);
            else if (key == "window") window_ = std::atoi(c);
            else if (key == "rope_theta") theta_ = (float) std::atof(c);
            else if (key == "eps") eps_ = (float) std::atof(c);
            else if (key == "conv_group") group_ = std::atoi(c);
            else if (key == "rank") rank_ = std::atoi(c);
            else if (key == "mask_token") mask_token_ = std::atoi(c);
            else if (key == "vocab") vocab_ = std::atoll(c);
            else if (key == "taps") {
                taps_.clear();
                for (const char* q = c; *q;) { taps_.push_back(std::atoi(q)); while (*q && *q != ',') ++q; if (*q == ',') ++q; }
            }
        } else if (std::sscanf(line.c_str(), "t %255s %63s %lld %lld %lld %lld", a, b, &v1, &v2, &v3, &v4) == 6) {
            Tensor t;
            t.name = a; t.dtype = b; t.rows = v1; t.cols = v2; t.off = (uint64_t) v3; t.bytes = (uint64_t) v4;
            tensors_.push_back(t);
            total = std::max(total, t.off + t.bytes);
        }
    }
    if (tensors_.empty() || NH_ != 32 || NKV_ != 8 || HD_ != 128 || rank_ != 256 || N_ != 2560 || L_ > 8 || window_ + 16 > kRing) {
        err = "dflash2: the pack's shape is not the Flash-Next drafter this engine was written for";
        return false;
    }
    pack_bytes_ = total;
    pack_path_ = path;
    if (shared != nullptr) {
        // Both sides parsed the same index: the tensor table and the layer/config rows decide that it is the same pack (a copied file
        // is fine; a re-packed or re-shaped one is not).  The head's row width is checked in bind(), where the head is known.
        bool same = shared->device_ == device_ && shared->blob_ != nullptr && shared->blob_owner_ != nullptr &&
                    shared->pack_bytes_ == total && shared->tensors_.size() == tensors_.size() && shared->N_ == N_ && shared->L_ == L_ &&
                    shared->NH_ == NH_ && shared->NKV_ == NKV_ && shared->HD_ == HD_ && shared->I_ == I_ && shared->window_ == window_ &&
                    shared->group_ == group_ && shared->rank_ == rank_ && shared->mask_token_ == mask_token_ && shared->taps_ == taps_ &&
                    shared->vocab_ == vocab_ && shared->theta_ == theta_ && shared->eps_ == eps_;
        for (size_t i = 0; same && i < tensors_.size(); ++i) {
            const Tensor& a = tensors_[i];
            const Tensor& b = shared->tensors_[i];
            same = a.name == b.name && a.dtype == b.dtype && a.rows == b.rows && a.cols == b.cols && a.off == b.off && a.bytes == b.bytes;
        }
        // A re-pack of the same shape would pass the index check above, so a sample of the file's own bytes is compared
        // too: a copy reads the same and passes, a different pack of the same shape does not.
        const std::vector<uint8_t> mine = sample_pack(path), theirs = sample_pack(shared->pack_path_);
        if (!mine.empty() && !theirs.empty() && mine != theirs) {
            err = "dflash2: incompatible shared weights (the pack bytes differ from " + shared->pack_path_ +
                  ", though its shape and layer table match; re-pack written in place?)";
            return false;
        }
        if (!same) { err = "dflash2: incompatible shared weights (a different device, pack size or layer table)"; return false; }
        blob_ = shared->blob_;
        blob_owner_ = shared->blob_owner_;
        shared_ = shared;
    } else {
        if (cudaMalloc((void**) &blob_, total) != cudaSuccess) { err = "dflash2: the pack does not fit in VRAM (" + std::to_string(total >> 20) + " MiB)"; return false; }
        vram_ += total;
        blob_owner_ = CudaPtr(blob_, CudaFree{});
        uint64_t max_bytes = 0;
        for (const Tensor& t : tensors_) max_bytes = std::max(max_bytes, t.bytes);
        std::vector<uint8_t> stage(max_bytes);
        uint8_t* tmp = nullptr;
        if (cudaMalloc((void**) &tmp, max_bytes) != cudaSuccess) { err = "dflash2: upload scratch"; return false; }
        struct TmpFree { uint8_t* p; ~TmpFree() { cudaFree(p); } } tmp_free{tmp};
        for (const Tensor& t : tensors_) {
            fseeko(f, data_off + (off_t) t.off, SEEK_SET);
            if (std::fread(stage.data(), 1, t.bytes, f) != t.bytes) { err = "dflash2: the pack is shorter than its index"; return false; }
            if (t.dtype == "q8_0") {   // GGUF blocks -> the tiled qs plane at the tensor's offset, then the d plane (same total bytes)
                if (cudaMemcpy(tmp, stage.data(), t.bytes, cudaMemcpyHostToDevice) != cudaSuccess) { err = "dflash2: weight upload failed"; return false; }
                kernels::df2_q8_tile(tmp, blob_ + t.off, blob_ + t.off + (uint64_t) t.rows * (uint64_t) t.cols, (int) t.cols, t.rows * t.cols / 32, nullptr);
                if (cudaDeviceSynchronize() != cudaSuccess) { err = "dflash2: weight conversion failed"; return false; }
            } else if (cudaMemcpy(blob_ + t.off, stage.data(), t.bytes, cudaMemcpyHostToDevice) != cudaSuccess) {
                err = "dflash2: weight upload failed";
                return false;
            }
        }
    }
    // the draft round runs while the verifier's async commit still has work on the card: a high-priority stream keeps it from queueing behind that
    // (CUDA by default; on HIP only with STRATA_DF2_PRIO=1, where the engine's other streams keep the default priority)
    int prio_lo = 0, prio_hi = 0;
    static const bool hi_prio = [] { const char* e = std::getenv("STRATA_DF2_PRIO"); return !(e && e[0] == '0'); }();
    cudaDeviceGetStreamPriorityRange(&prio_lo, &prio_hi);
    if (cudaStreamCreateWithPriority(&cs_, cudaStreamNonBlocking, hi_prio ? prio_hi : 0) != cudaSuccess ||
        cudaStreamCreateWithPriority(&cs2_, cudaStreamNonBlocking, hi_prio ? prio_hi : 0) != cudaSuccess) {
        err = "dflash2: stream";
        return false;
    }
    if (cudaEventCreateWithFlags(&ev_fork_, cudaEventDisableTiming) != cudaSuccess || cudaEventCreateWithFlags(&ev_join_, cudaEventDisableTiming) != cudaSuccess) {
        err = "dflash2: events";
        return false;
    }
    for (int l = 0; l < 8; ++l)
        if (cudaEventCreateWithFlags(&ev_ctx_[l], cudaEventDisableTiming) != cudaSuccess) { err = "dflash2: events"; return false; }
    if (const char* e = std::getenv("STRATA_DF2_PROFILE"); e && e[0] == '1') prof_ = new Prof();
    if (const char* e = std::getenv("STRATA_DF2_TIMING"); e && e[0] == '1') {
        cudaEvent_t first = nullptr, second = nullptr;
        timing_ = cudaEventCreate(&first) == cudaSuccess && cudaEventCreate(&second) == cudaSuccess;
        if (timing_) {
            ev_a_ = first;
            ev_b_ = second;
        } else {
            if (first) (void) cudaEventDestroy(first);
            if (second) (void) cudaEventDestroy(second);
            (void) cudaGetLastError();
        }
    }
    if (!alloc(err)) return false;
    if (shared != nullptr)
        std::fprintf(stderr, "strata dflash2: shared pack weights, %.0f MiB of private buffers (ring, logits and graphs stay this slot's)\n",
                     (double) vram_ / 1048576.0);
    rollback.commit = true;
    return true;
}

bool Dflash2Drafter::alloc(std::string& err) {
    std::vector<std::pair<void**, size_t>> requests;
    auto add = [&](auto** p, size_t n) {
        requests.push_back({(void**) p, n * sizeof(**p)});
    };
    const size_t B = kRows;
    add(&d_meta_, 32); add(&xq_, kernels::native_q8_1_bytes(N_, kRows) + 4096);
    add(&xqp_, (size_t) 5 * N_ * 8 + 4096); add(&xdp_, (size_t) 5 * N_ / 32 * 8 + 64);
    add(&xqc_, (size_t) 5 * N_ * 8 + 4096); add(&xdc_, (size_t) 5 * N_ / 32 * 8 + 64);
    add(&rt_ctx_, B * 128); add(&rt_blk_, B * 128);
    add(&fcy_, B * N_); add(&hctx_, B * N_); add(&kc_, B * NKV_ * HD_); add(&vc_, B * NKV_ * HD_);
    add(&h_, B * N_); add(&xn_, B * N_); add(&xc_, B * N_); add(&dyn_, B * 640);
    add(&q_, B * NH_ * HD_); add(&qn_, B * NH_ * HD_); add(&k_, B * NKV_ * HD_); add(&v_, B * NKV_ * HD_);
    add(&kblk_, B * NKV_ * HD_); add(&ao_, B * NH_ * HD_); add(&o_, B * N_); add(&gate_, B * I_); add(&up_, B * I_);
    add(&mlp_, B * N_); add(&part_, (size_t) kernels::kDf2MaxChunks * 8 * 32 * 130); add(&fn_, B * N_);
    add(&topval_, kDrafts * 16); add(&toptok_, kDrafts * 16); add(&hsel_, kDrafts * rank_);
    add(&topscratch_, (size_t) kDrafts * kernels::kDf2TopBlocks * kernels::kDf2TopK * 2);
    for (int l = 0; l < L_; ++l) {
        add(&kring_[l], (size_t) kRing * NKV_ * HD_);
        add(&vring_[l], (size_t) kRing * NKV_ * HD_);
    }

    std::vector<void*> made;
    made.reserve(requests.size());
    auto free_made = [&] {
        for (void* p : made) if (p != nullptr) cudaFree(p);
        made.clear();
    };
    size_t total = 0;
    for (const auto& [slot, bytes] : requests) {
        void* p = nullptr;
        if (cudaMalloc(&p, bytes) != cudaSuccess) {
            free_made();
            err = "dflash2: the drafter's buffers do not fit in VRAM";
            return false;
        }
        made.push_back(p);
        total += bytes;
    }
    void* meta_host = nullptr;
    void* meta_device = nullptr;
    if (!mapped(32 * 4, &meta_host, &meta_device)) {
        free_made();
        err = "dflash2: mapped staging";
        return false;
    }
    void* out_host = nullptr;
    void* out_device = nullptr;
    if (!mapped(64 * 4, &out_host, &out_device)) {
        strata::core::host_free_guarded(meta_host);
        free_made();
        err = "dflash2: mapped output";
        return false;
    }

    for (size_t i = 0; i < requests.size(); ++i) *requests[i].first = made[i];
    h_meta_ = (int32_t*) meta_host;
    m_meta_ = (int32_t*) meta_device;
    h_out_ = (int32_t*) out_host;
    out_tok_ = (int32_t*) out_device;       // the select kernel writes the 7 picks here (host-visible) ...
    out_prob_ = (float*) out_device + 16;   // ... and their probabilities at +16 ints
    vram_ += total;

    auto release_buffers = [&] {
        for (const auto& [slot, bytes] : requests) {
            (void) bytes;
            if (*slot != nullptr) cudaFree(*slot);
            *slot = nullptr;
        }
        if (h_meta_ != nullptr) strata::core::host_free_guarded(h_meta_);
        if (h_out_ != nullptr) strata::core::host_free_guarded(h_out_);
        h_meta_ = nullptr;
        m_meta_ = nullptr;
        h_out_ = nullptr;
        out_tok_ = nullptr;
        out_prob_ = nullptr;
        vram_ -= total;
    };
    auto zero = [](void* p, size_t bytes) { return cudaMemset(p, 0, bytes) == cudaSuccess; };
    const bool zeroed =
        zero(rt_ctx_, B * 128 * 4) && zero(xqp_, (size_t) 5 * N_ * 8 + 4096) &&
        zero(xdp_, ((size_t) 5 * N_ / 32 * 8 + 64) * 4) && zero(xqc_, (size_t) 5 * N_ * 8 + 4096) &&
        zero(xdc_, ((size_t) 5 * N_ / 32 * 8 + 64) * 4);
    bool rings_zeroed = zeroed;
    for (int l = 0; rings_zeroed && l < L_; ++l)
        rings_zeroed = zero(kring_[l], (size_t) kRing * NKV_ * HD_ * 4) &&
                       zero(vring_[l], (size_t) kRing * NKV_ * HD_ * 4);
    if (!rings_zeroed) {
        release_buffers();
        err = "dflash2: the private buffers could not be initialized";
        return false;
    }
    const cudaError_t sync = cudaDeviceSynchronize();
    if (sync != cudaSuccess) {
        release_buffers();
        err = std::string("dflash2: private buffer initialization: ") + cudaGetErrorString(sync);
        return false;
    }
    return true;
}

bool Dflash2Drafter::bind_raw(EmbedFn embed, const void* head_w, int head_type, int64_t head_rows, const std::vector<int32_t>& vocab, std::string& err) {
    const OnDevice on_device(device_);
    if (!dflash_device_ok(on_device, device_, "raw binding", err)) return false;
    const int64_t n_rows = vocab.empty() ? head_rows : (int64_t) vocab.size();
    if (n_rows <= 0) {
        err = "dflash2: the draft head has no rows";
        return false;
    }
    if ((n_rows + kernels::kDf2TopBlocks - 1) / kernels::kDf2TopBlocks > 4096) {
        err = "dflash2: the head has too many rows for the top-k";
        return false;
    }
    CudaPtr dvocab_pending;
    int32_t* dvocab_new = nullptr;
    std::vector<int32_t> dvocab_host_new;
    if (!vocab.empty()) {
        if (cudaMalloc((void**) &dvocab_new, vocab.size() * 4) != cudaSuccess) {
            err = "dflash2: draft vocabulary";
            return false;
        }
        dvocab_pending = CudaPtr(dvocab_new, CudaFree{});
        if (cudaMemcpy(dvocab_new, vocab.data(), vocab.size() * 4, cudaMemcpyHostToDevice) != cudaSuccess) {
            err = "dflash2: draft vocabulary upload";
            return false;
        }
        dvocab_host_new = vocab;
    }
    if (!alloc_logits(n_rows, err)) return false;
    embed_ = std::move(embed);
    head_w_ = head_w;
    head_type_ = head_type;
    n_dvocab_ = n_rows;
    vram_ -= dvocab_bytes_;
    dvocab_owner_.reset();
    dvocab_ = nullptr;
    dvocab_host_.clear();
    dvocab_bytes_ = 0;
    if (dvocab_pending) {
        dvocab_owner_ = std::move(dvocab_pending);
        dvocab_ = dvocab_new;
        dvocab_host_ = std::move(dvocab_host_new);
        dvocab_bytes_ = vocab.size() * 4;
        vram_ += dvocab_bytes_;
    }
    bound_ = true;
    return true;
}

bool Dflash2Drafter::alloc_logits(int64_t n_dvocab, std::string& err) {
    const size_t need = (size_t) kDrafts * (size_t) n_dvocab * sizeof(float);
    if (logits_ != nullptr && logits_cap_ >= need) return true;      // a re-bind at the same (or a narrower) width
    float* replacement = nullptr;
    if (cudaMalloc((void**) &replacement, need) != cudaSuccess) {
        err = "dflash2: the draft logits do not fit";
        return false;
    }
    // A re-bind may follow a wider head: keep the working buffer until its replacement exists.
    if (logits_ != nullptr) {
        cudaFree(logits_);
        vram_ -= logits_cap_;
    }
    logits_ = replacement;
    logits_cap_ = need;
    vram_ += need;
    return true;
}

bool Dflash2Drafter::bind(const WeightTable& wt, const NativeHead* head, const std::string& vocab_file, std::string& err) {
    const OnDevice on_device(device_);
    if (!dflash_device_ok(on_device, device_, "binding", err)) return false;
    if (head == nullptr || !head->loaded()) { err = "dflash2: the drafter needs the native head (--native)"; return false; }
    const WeightRef* we = wt.find("token_embd.weight");
    if (we == nullptr) { err = "dflash2: token_embd.weight is missing"; return false; }
    EmbedFn embed = [we](const int32_t* tok, int n, float* out, cudaStream_t cs) {
        if (const NativeEmbed* ne = native_embed()) {
            ne->gather_dev(tok, n, out, cs);
        } else {
            const auto* codes = (const uint8_t*) we->data;
            const auto* scales = (const float*) (codes + we->codes_bytes);
            const auto* offsets = we->has_offset ? (const float*) (codes + we->codes_bytes + we->scales_bytes) : nullptr;
            kernels::embedding_gather_dev(codes, scales, offsets, tok, n, we->ne0, we->code_bits, we->code_bias, we->group_elems,
                                          (uint64_t) (we->ne0 / (8 / we->code_bits)), (uint64_t) (we->ne0 / we->group_elems), out, cs);
        }
    };
    std::vector<int32_t> vocab;
    if (!vocab_file.empty()) {
        if (std::FILE* vf = std::fopen(vocab_file.c_str(), "rb")) {
            std::fseek(vf, 0, SEEK_END);
            const long size = std::ftell(vf);
            std::fseek(vf, 0, SEEK_SET);
            if (size >= 4 && size % 4 == 0) {
                vocab.resize((size_t) size / 4);
                if (std::fread(vocab.data(), 4, vocab.size(), vf) != vocab.size()) vocab.clear();
            }
            std::fclose(vf);
        }
    }
    const void* hw = head->weights();
    const int64_t row_bytes = (int64_t) head->row_bytes();
    int64_t head_rows = row_bytes > 0 ? (int64_t) (head->weight_bytes() / (uint64_t) row_bytes) : 0;
    if (shared_ != nullptr) {
        // same head object and row width; when the pack's draft vocabulary was bound, the same token subset (the mapping has to match:
        // rows gathered for a different list would be silently wrong); otherwise the same whole-head row count.
        bool same = shared_->bound_ && shared_->device_ == device_ && shared_->head_src_ == head && shared_->head_type_ == head->type() &&
                    shared_->head_row_bytes_ == row_bytes;
        if (same) same = shared_->dvocab_ != nullptr ? (vocab == shared_->dvocab_host_) : (vocab.empty() && shared_->n_dvocab_ == head_rows);
        if (!same) { err = "dflash2: incompatible shared draft head (a different head, row width or draft vocabulary)"; return false; }
        if (!alloc_logits(shared_->n_dvocab_, err)) return false;
        vram_ -= dvocab_bytes_;
        vram_ -= dhead_bytes_;
        dvocab_owner_.reset();
        dhead_owner_.reset();
        embed_ = std::move(embed);
        head_w_ = shared_->head_w_;
        head_type_ = head->type();
        n_dvocab_ = shared_->n_dvocab_;
        dvocab_ = shared_->dvocab_;
        dvocab_owner_ = shared_->dvocab_owner_;
        dvocab_host_ = shared_->dvocab_host_;
        dhead_ = shared_->dhead_;
        dhead_owner_ = shared_->dhead_owner_;
        dvocab_bytes_ = 0;   // shared owners keep the rows alive; this instance did not allocate them
        dhead_bytes_ = 0;
        head_src_ = head;
        head_row_bytes_ = row_bytes;
        bound_ = true;
        std::fprintf(stderr, "strata dflash2: shared %lld-token draft head, %.0f MiB of private buffers\n", (long long) n_dvocab_,
                     (double) vram_ / 1048576.0);
        return true;
    }
    if (!vocab.empty()) {
        int32_t* dv = nullptr;
        uint8_t* dh = nullptr;
        if (cudaMalloc((void**) &dv, vocab.size() * 4) != cudaSuccess || cudaMalloc((void**) &dh, (size_t) ((int64_t) vocab.size() * row_bytes)) != cudaSuccess) {
            if (dv) cudaFree(dv);
            if (dh) cudaFree(dh);
            err = "dflash2: the draft vocabulary's head rows do not fit";
            return false;
        }
        struct VocabFree {
            int32_t* p;
            ~VocabFree() { if (p != nullptr) cudaFree(p); }
        } vocab_free{dv};
        CudaPtr dhead_pending(dh, CudaFree{});
        if (cudaMemcpy(dv, vocab.data(), vocab.size() * 4, cudaMemcpyHostToDevice) != cudaSuccess) {
            err = "dflash2: the draft vocabulary upload failed";
            return false;
        }
        kernels::gather_rows((const uint8_t*) hw, row_bytes, dv, (int64_t) vocab.size(), dh, nullptr);
        const cudaError_t sync = cudaDeviceSynchronize();
        if (sync != cudaSuccess) {
            err = std::string("dflash2: draft head gather: ") + cudaGetErrorString(sync);
            return false;
        }
        hw = dh;
        head_rows = (int64_t) vocab.size();
        if (!bind_raw(embed, hw, head->type(), head_rows, vocab, err)) return false;
        vram_ -= dhead_bytes_;
        dhead_owner_.reset();
        dhead_owner_ = std::move(dhead_pending);
        dhead_ = dh;
        dhead_bytes_ = (size_t) ((int64_t) vocab.size() * row_bytes);
        vram_ += dhead_bytes_;
    } else {
        if (!bind_raw(embed, hw, head->type(), head_rows, vocab, err)) return false;
        vram_ -= dhead_bytes_;
        dhead_owner_.reset();
        dhead_ = nullptr;
        dhead_bytes_ = 0;
    }
    head_src_ = head;
    head_row_bytes_ = row_bytes;
    std::fprintf(stderr, "strata dflash2: %lld-token draft head (%.1f MiB), drafter %.0f MiB of VRAM\n", (long long) n_dvocab_,
                 (double) (n_dvocab_ * row_bytes) / 1048576.0, (double) vram_ / 1048576.0);
    return true;
}

void Dflash2Drafter::gemm(std::initializer_list<const char*> names, std::initializer_list<float*> outs, int ncols, cudaStream_t cs, const uint8_t* xq, const float* xd) {
    kernels::Df2Mat mats[3];
    int n = 0, n_in = 0;
    auto o = outs.begin();
    for (const char* name : names) {
        const Tensor& t = find(name);
        mats[n] = kernels::Df2Mat{blob_ + t.off, *o++, (int) t.rows};
        if (n == 0) n_in = (int) t.cols;
        ++n;
    }
    kernels::df2_gemm(mats, n, xq, xd, n_in, ncols, cs);
}

bool Dflash2Drafter::record_context(const float* taps, int n, cudaStream_t cs, std::string& err) {
    using namespace kernels;
    if (skip_part("ctx")) return true;
    try {
        const int tap_n = (int) taps_.size() * N_;
        df2_rope_table(d_meta_ + 0, n, theta_, rt_ctx_, cs);
        stamp("df2_rope_table");
        df2_quant_rows(taps, tap_n, n, xqc_, xdc_, cs);
        stamp("df2_quant_rows");
        gemm({"fc.weight"}, {fcy_}, n, cs, xqc_, xdc_);
        stamp("gemm:fc");
        df2_rms_quant(fcy_, f32("hidden_norm.weight"), nullptr, n, N_, eps_, xqc_, xdc_, cs);
        stamp("df2_rms_quant");
        for (int l = 0; l < L_; ++l) {
            const std::string p = "layers." + std::to_string(l) + ".";
            const std::string kp = p + "self_attn.k_proj.weight", vp = p + "self_attn.v_proj.weight";
            gemm({kp.c_str(), vp.c_str()}, {kc_, vc_}, n, cs, xqc_, xdc_);
            stamp("gemm:kp");
            df2_kv_ring(kc_, vc_, f32(p + "self_attn.k_norm.weight"), d_meta_ + 0, rt_ctx_, kring_[l], vring_[l], kRing, n, NKV_, eps_, cs);
            stamp("df2_kv_ring");
            if (fork_) cudaEventRecord(ev_ctx_[l], cs);   // layer l's keys and values are in the ring
        }
    } catch (const std::exception& e) {
        err = e.what();
        return false;
    }
    return true;
}

bool Dflash2Drafter::record_block(cudaStream_t cs, std::string& err) {
    using namespace kernels;
    try {
        const int B = kRows;
        df2_rope_table(d_meta_ + 8, B, theta_, rt_blk_, cs);
        stamp("df2_rope_table");
        embed_(d_meta_ + 24, B, h_, cs);
        stamp("embed_");
        df2_rms_quant(h_, f32("layers.0.input_layernorm.weight"), xn_, B, N_, eps_, xqp_, xdp_, cs);
        stamp("df2_rms_quant");
        for (int l = 0; l < L_; ++l) {
            const std::string p = "layers." + std::to_string(l) + ".";
            const float* base_a = f32(p + "attention_conv.base_kernel");
            const float* base_m = f32(p + "mlp_conv.base_kernel");
            const std::string akp = p + "attention_conv.kernel_projection.weight", mkp = p + "mlp_conv.kernel_projection.weight";
            const std::string qp = p + "self_attn.q_proj.weight", kp = p + "self_attn.k_proj.weight", vp = p + "self_attn.v_proj.weight";
            const std::string gp = p + "mlp.gate_proj.weight", up = p + "mlp.up_proj.weight";
            if (!skip_part("tiny")) gemm({akp.c_str()}, {dyn_}, B, cs, xqp_, xdp_);
            stamp("gemm:akp");
            if (!skip_part("small")) df2_conv_quant(xn_, dyn_, 640, base_a, B, N_, group_, xqp_, xdp_, cs);
            stamp("df2_conv_quant");
            if (!skip_part("qkvo")) gemm({qp.c_str(), kp.c_str(), vp.c_str()}, {q_, k_, v_}, B, cs, xqp_, xdp_);
            stamp("gemm:qp");
            if (!skip_part("small")) df2_norm_rope_qk(q_, k_, f32(p + "self_attn.q_norm.weight"), f32(p + "self_attn.k_norm.weight"), rt_blk_, qn_, kblk_, B, NH_, NKV_, eps_, cs);
            stamp("df2_norm_rope_qk");
            if (fork_) cudaStreamWaitEvent(cs, ev_ctx_[l], 0);   // the context update has written this layer's ring
            if (!skip_part("attn")) df2_attention(qn_, kring_[l], vring_[l], kblk_, v_, d_meta_ + 17, kRing, window_, part_, xqp_, xdp_, cs);
            stamp("df2_attention");
            if (!skip_part("qkvo")) gemm({(p + "self_attn.o_proj.weight").c_str()}, {o_}, B, cs, xqp_, xdp_);
            stamp("gemm:o_proj");
            if (!skip_part("small")) df2_conv_rms_quant(o_, dyn_ + 320, 640, base_a + 2 * N_, h_, f32(p + "post_attention_layernorm.weight"), xn_, B, N_, group_, eps_, xqp_, xdp_, cs);
            stamp("df2_conv_rms_quant");
            if (!skip_part("tiny")) gemm({mkp.c_str()}, {dyn_}, B, cs, xqp_, xdp_);
            stamp("gemm:mkp");
            if (!skip_part("small")) df2_conv_quant(xn_, dyn_, 640, base_m, B, N_, group_, xqp_, xdp_, cs);
            stamp("df2_conv_quant");
            if (!skip_part("mlp")) gemm({gp.c_str(), up.c_str()}, {gate_, up_}, B, cs, xqp_, xdp_);
            stamp("gemm:gp");
            if (!skip_part("small")) df2_swiglu_quant(gate_, up_, B, I_, xqp_, xdp_, cs);
            stamp("df2_swiglu_quant");
            if (!skip_part("mlp")) gemm({(p + "mlp.down_proj.weight").c_str()}, {mlp_}, B, cs, xqp_, xdp_);
            stamp("gemm:mlp.down_proj");
            // the next layer's input norm, or after the last layer the final norm (-> fn_, the rows the head and the selector read)
            const std::string next_gamma = l + 1 < L_ ? "layers." + std::to_string(l + 1) + ".input_layernorm.weight" : std::string("norm.weight");
            if (!skip_part("small")) df2_conv_rms_quant(mlp_, dyn_ + 320, 640, base_m + 2 * N_, h_, f32(next_gamma), l + 1 < L_ ? xn_ : fn_, B, N_, group_, eps_, xqp_, xdp_, cs);
            stamp("df2_conv_rms_quant");
        }
        native_quantize_q8_1(fn_ + N_, xq_, N_, kDrafts, cs);   // rows 1..7, the head's format
        df2_quant_rows(fn_ + N_, N_, kDrafts, xqp_, xdp_, cs);   // and the selector projection's
        if (!skip_part("head")) native_mmvq(head_type_, head_w_, xq_, logits_, N_, (int) n_dvocab_, kDrafts, cs);
        stamp("native_mmvq");
        df2_topk(logits_, (int) n_dvocab_, dvocab_, kDrafts, topscratch_, topval_, toptok_, cs);
        stamp("df2_topk");
        gemm({"candidate_selector.hidden_projection.weight"}, {hsel_}, kDrafts, cs, xqp_, xdp_);
        stamp("gemm:candidate_selector.hidden_projection");
        df2_select(hsel_, topval_, toptok_, reinterpret_cast<const uint16_t*>(blob_ + find("candidate_selector.predecessor_codebook").off),
                   reinterpret_cast<const uint16_t*>(blob_ + find("candidate_selector.successor_codebook").off), rank_, kDrafts, d_meta_ + 16, out_tok_,
                   out_prob_, cs);
    } catch (const std::exception& e) {
        err = e.what();
        return false;
    }
    return true;
}

bool Dflash2Drafter::capture(int n, const float* taps, std::string& err) {
    if (exec_[n]) return true;
    if (cudaStreamBeginCapture(cs_, cudaStreamCaptureModeThreadLocal) != cudaSuccess) { err = "dflash2: begin capture"; return false; }
    kernels::copy_i32_from_mapped(d_meta_, m_meta_, 32, cs_);
    // the context update (fc, the keys and values) and the block's front run side by side: they meet before each layer's attention
    // measured: -0.3 ms a round on an RTX 3060, +0.0..0.7 on a Strix Halo (gfx1151), so the HIP builds keep one stream (STRATA_DF2_FORK=1 / 0 overrides)
    static const bool fork_default = true;
    static const bool fork_on = [] { const char* e = std::getenv("STRATA_DF2_FORK"); return e && e[0] ? e[0] == '1' : fork_default; }();
    strata::core::ScopedValueRestore<bool> restore_fork(fork_);
    fork_ = fork_on && prof_ == nullptr && cs2_ != nullptr;
    bool ok = false;
    bool fork_started = false;
    bool fork_ready = true;
    try {
        if (fork_) {
            const cudaError_t record = cudaEventRecord(ev_fork_, cs_);
            const cudaError_t wait = record == cudaSuccess ? cudaStreamWaitEvent(cs2_, ev_fork_, 0) : record;
            if (record != cudaSuccess || wait != cudaSuccess) {
                err = std::string("dflash2: capture fork: ") +
                      cudaGetErrorString(record != cudaSuccess ? record : wait);
                fork_ready = false;
            } else {
                fork_started = true;
            }
        }
        if (fork_ready) ok = record_context(taps, n, fork_started ? cs2_ : cs_, err);
    } catch (const std::exception& e) {
        err = std::string("dflash2: capture: ") + e.what();
        ok = false;
    } catch (...) {
        err = "dflash2: capture: unknown exception";
        ok = false;
    }
    if (fork_started) {
        const cudaError_t record = cudaEventRecord(ev_join_, cs2_);
        const cudaError_t wait = record == cudaSuccess ? cudaStreamWaitEvent(cs_, ev_join_, 0) : record;
        if (record != cudaSuccess || wait != cudaSuccess) {
            err = std::string("dflash2: capture fork join: ") +
                  cudaGetErrorString(record != cudaSuccess ? record : wait);
            ok = false;
        }
    }
    if (ok) {
        try {
            ok = record_block(cs_, err);
        } catch (const std::exception& e) {
            err = std::string("dflash2: capture: ") + e.what();
            ok = false;
        } catch (...) {
            err = "dflash2: capture: unknown exception";
            ok = false;
        }
    }
    restore_fork.restore();
    if (!ok && err.empty()) err = "dflash2: capture recording failed";
    cudaGraph_t graph = nullptr;
    const cudaError_t ce = cudaStreamEndCapture(cs_, &graph);
    if (!ok) { if (graph) cudaGraphDestroy(graph); return false; }
    if (ce != cudaSuccess) {
        if (graph) cudaGraphDestroy(graph);
        err = std::string("dflash2: capture: ") + cudaGetErrorString(ce);
        return false;
    }
    if (!strata::core::instantiate_graph_exec(
            graph, exec_[n], "dflash2: capture", err))
        return false;
    return strata::core::upload_graph_exec(exec_[n], cs_, "dflash2: capture", err);
}

bool Dflash2Drafter::prepare(const float* taps, std::string& err) {
    if (!bound_) { err = "dflash2: not bound"; return false; }
    if (prof_ != nullptr) return true;   // the profile runs without graphs
    const OnDevice on_device(device_);
    if (!dflash_device_ok(on_device, device_, "graph preparation", err)) return false;
    const Clock::time_point t0 = Clock::now();
    for (int i = 0; i < 8; ++i) h_meta_[i] = i;
    for (int n = 1; n <= kRows; ++n)
        if (!capture(n, taps, err)) return false;
    graph_taps_ = taps;
    ms_capture = ms_since(t0);
    return true;
}

bool Dflash2Drafter::context(const float* taps, int64_t n, int64_t pos0, std::string& err) {
    if (device_ < 0 || !bound_) { err = "dflash2: the drafter was released"; return false; }
    const OnDevice on_device(device_);
    if (!dflash_device_ok(on_device, device_, "context staging", err)) return false;
    const int tap_n = (int) taps_.size() * N_;
    for (int64_t r0 = 0; r0 < n; r0 += kRows) {
        const int m = (int) std::min<int64_t>(kRows, n - r0);
        for (int i = 0; i < m; ++i) h_meta_[i] = (int32_t) (pos0 + r0 + i);
        std::atomic_thread_fence(std::memory_order_seq_cst);
        kernels::copy_i32_from_mapped(d_meta_, m_meta_, 32, cs_);
        if (!record_context(taps + (size_t) r0 * (size_t) tap_n, m, cs_, err)) return false;
        if (cudaStreamSynchronize(cs_) != cudaSuccess) { err = std::string("dflash2: ") + cudaGetErrorString(cudaGetLastError()); return false; }
    }
    have_ctx_ = std::max<int64_t>(have_ctx_, pos0 + n);
    return true;
}

bool Dflash2Drafter::draft(const float* taps, int n_new, int64_t pos0, int32_t anchor, int32_t* drafts, float* probs, std::string& err) {
    if (drafts == nullptr) { err = "dflash2: draft needs the drafts buffer"; return false; }
    if (!draft_launch(taps, n_new, pos0, anchor, err)) return false;
    return draft_collect(drafts, probs, err);
}

bool Dflash2Drafter::draft_launch(const float* taps, int n_new, int64_t pos0, int32_t anchor, std::string& err) {
    if (!bound_) { err = "dflash2: not bound"; return false; }
    if (n_new < 1 || n_new > kRows) { err = "dflash2: draft rows out of range"; return false; }
    if (pending_) { err = "dflash2: draft_launch while a draft is in flight (draft_collect first)"; return false; }
    const OnDevice on_device(device_);
    if (!dflash_device_ok(on_device, device_, "a draft launch", err)) return false;
    const Clock::time_point t0 = Clock::now();
    if (graph_taps_ != nullptr && graph_taps_ != taps) { err = "dflash2: the taps buffer moved after capture"; return false; }
    const bool profile = prof_ != nullptr && prof_->enabled;
    if (!profile && !exec_[n_new]) {
        if (!lazy_capture_) {
            err = "dflash2: no round graph for " + std::to_string(n_new) + " rows and lazy capture is off (prepare(taps_dev) first)";
            return false;
        }
        // the staging must hold valid positions while the capture runs the (empty) kernels' parameter checks
        for (int i = 0; i < 8; ++i) h_meta_[i] = (int32_t) (pos0 + i);
        if (!capture(n_new, taps, err)) return false;
        graph_taps_ = taps;
    }
    const int64_t s = pos0 + n_new;
    for (int i = 0; i < 8; ++i) { h_meta_[i] = (int32_t) (pos0 + std::min(i, n_new - 1)); h_meta_[8 + i] = (int32_t) (s + i); }
    h_meta_[16] = anchor;
    h_meta_[17] = (int32_t) s;
    h_meta_[24] = anchor;
    for (int i = 1; i < 8; ++i) h_meta_[24 + i] = mask_token_;
    for (int i = 0; i < kDrafts; ++i) { ((volatile int32_t*) h_out_)[i] = -1; }
    std::atomic_thread_fence(std::memory_order_seq_cst);
    if (timing_) cudaEventRecord(ev_a_, cs_);
    if (profile) {   // the same round without its graph, an event after every stage; this mode synchronizes here
        cudaStreamSynchronize(cs_);
        for (cudaEvent_t e : prof_->ev) cudaEventDestroy(e);   // the stamps of context() calls are not a round
        prof_->ev.clear();
        prof_->name.clear();
        kernels::copy_i32_from_mapped(d_meta_, m_meta_, 32, cs_);
        stamp("start");
        if (!record_context(taps, n_new, cs_, err) || !record_block(cs_, err)) return false;
        prof_collect();
    } else
    if (cudaGraphLaunch(exec_[n_new], cs_) != cudaSuccess) { err = std::string("dflash2 draft: ") + cudaGetErrorString(cudaGetLastError()); return false; }
    if (timing_) cudaEventRecord(ev_b_, cs_);
    (void) cudaStreamQuery(cs_);
    pending_ = true;
    pending_pos1_ = s;
    pending_timing_ = timing_;
    pending_t0_ = t0;
    return true;
}

bool Dflash2Drafter::draft_collect(int32_t* drafts, float* probs, std::string& err) {
    if (!pending_) { err = "dflash2: draft_collect without a draft_launch"; return false; }
    if (drafts == nullptr) { err = "dflash2: draft_collect needs the drafts buffer"; return false; }
    const OnDevice on_device(device_);
    if (!dflash_device_ok(on_device, device_, "a draft collect", err)) return false;
    if (cudaStreamSynchronize(cs_) != cudaSuccess) {
        pending_ = false;
        err = std::string("dflash2 draft: ") + cudaGetErrorString(cudaGetLastError());
        return false;
    }
    for (int i = 0; i < kDrafts; ++i) {
        drafts[i] = ((volatile int32_t*) h_out_)[i];
        if (probs) probs[i] = ((volatile float*) h_out_)[16 + i];
    }
    if (pending_timing_) {
        float ms = 0;
        if (cudaEventElapsedTime(&ms, ev_a_, ev_b_) == cudaSuccess) ms_gpu += ms;
    }
    have_ctx_ = std::max<int64_t>(have_ctx_, pending_pos1_);
    ms_draft += ms_since(pending_t0_);
    ++rounds;
    pending_ = false;
    return true;
}

bool Dflash2Drafter::debug_rows(std::vector<float>& rows, std::string& err) {
    rows.resize((size_t) kRows * (size_t) N_);
    if (cudaMemcpy(rows.data(), fn_, rows.size() * 4, cudaMemcpyDeviceToHost) != cudaSuccess) { err = "dflash2: debug copy"; return false; }
    return true;
}

}  // namespace strata::core
