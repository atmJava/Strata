#include "strata/core/native_head.hpp"
#include "strata/artifact/gguf_reader.hpp"
#include "strata/kernels/iq_kernels.hpp"
#include "strata/kernels/native_mmvq.hpp"
#include "strata/kernels/verify_kernels.hpp"
#include "strata/platform/memory.hpp"

#include <cuda_runtime.h>
#include <climits>
#include <cstdio>
#include <cstring>
#include <exception>

namespace strata::core {

NativeHead::~NativeHead() {
    unload();
}

void NativeHead::unload() {
    int current = -1;
    cudaGetDevice(&current);
    if (weights_) strata::kernels::native_q6_k_unpack(weights_);
    if (scratch_) cudaFree(scratch_);
    if (weights_) cudaFree(weights_);
    if (peer_weights_ && peer_device_ >= 0) {
        cudaSetDevice(peer_device_);
        cudaFree(peer_weights_);
        cudaSetDevice(current);
    }
    scratch_ = nullptr;
    weights_ = nullptr;
    peer_weights_ = nullptr;
    bytes_ = 0;
    n_in_ = n_out_ = 0;
    type_ = -1;
    device_ = -1;
    peer_device_ = -1;
    split_rows0_ = split_rows1_ = 0;
}

bool NativeHead::load(const std::vector<std::string>& shards, int64_t n_in, int64_t n_out, std::string& err) {
    if (loaded()) { err = "native head is already loaded"; return false; }
    if (n_in <= 0 || n_out <= 0 || n_in > INT_MAX || n_out > INT_MAX || n_in % 256) {
        err = "native head requires positive int32 dimensions and whole 256-value rows";
        return false;
    }
    try {
        // The architecture is the metadata shard's; output.weight comes from whichever shard holds it (shard 2
        // of Unsloth's UD-Q4_K_XL, whose shard 1 holds no tensor).  GgufModel refuses a duplicate across shards.
        const strata::GgufModel model(shards);
        err = strata::check_engine_architecture(model.meta());
        if (!err.empty()) return false;
        size_t at = 0;
        const strata::TensorInfo* tensor = model.find("output.weight", &at);
        const strata::GgufFile& gguf = model.shard(at);
        if (!tensor || !strata::kernels::native_mmvq_supported((int) tensor->type) || tensor->shape.size() != 2 ||
            tensor->shape[0] != (uint64_t) n_in || tensor->shape[1] != (uint64_t) n_out) {
            err = "native head: expected a natively supported output.weight with the canonical head dimensions";
            return false;
        }
        const uint64_t bytes = strata::kernels::native_mmvq_weight_bytes((int) tensor->type, (int) n_in, (int) n_out);
        const uint64_t payload = gguf.file_size() - gguf.data_start();
        if (tensor->offset > payload || bytes > payload - tensor->offset) {
            err = "native head: truncated output.weight payload";
            return false;
        }
        void* weights = nullptr;
        void* scratch = nullptr;
        cudaError_t status = cudaMalloc(&weights, bytes);
        if (status == cudaSuccess)
            status = cudaMalloc(&scratch, strata::kernels::native_q8_1_bytes((int) n_in, 1));
        strata::platform::advise_willneed(gguf.tensor_data(*tensor), bytes);
        if (status == cudaSuccess)
            status = cudaMemcpy(weights, gguf.tensor_data(*tensor), bytes, cudaMemcpyHostToDevice);
        if (status != cudaSuccess) {
            if (scratch) cudaFree(scratch);
            if (weights) cudaFree(weights);
            err = std::string("native head upload: ") + cudaGetErrorString(status);
            return false;
        }
        weights_ = weights;
        { int d = -1; if (cudaGetDevice(&d) == cudaSuccess) device_ = d; }
        scratch_ = scratch;
        bytes_ = bytes;
        n_in_ = (int) n_in;
        n_out_ = (int) n_out;
        type_ = (int) tensor->type;
        if (type_ == 14 && strata::kernels::native_q6_k_packed_enabled())   // STRATA_Q6_PACKED=1
            strata::kernels::native_q6_k_pack(weights_, n_in_, n_out_, "output head");
        return true;
    } catch (const std::exception& error) {
        err = std::string("native head: ") + error.what();
        return false;
    }
}

bool NativeHead::load_split(const std::vector<std::string>& shards, int64_t n_in, int64_t n_out, int peer,
                            std::string& err) {
    if (loaded()) { err = "native head is already loaded"; return false; }
    if (n_in <= 0 || n_out <= 0 || n_in > INT_MAX || n_out > INT_MAX || n_in % 256 || n_out % 2 != 0) {
        err = "split native head requires positive even int32 dimensions and whole 256-value rows";
        return false;
    }
    int main_device = -1;
    if (cudaGetDevice(&main_device) != cudaSuccess || peer < 0 || peer == main_device) {
        err = "split native head requires two distinct devices";
        return false;
    }
    try {
        const strata::GgufModel model(shards);
        err = strata::check_engine_architecture(model.meta());
        if (!err.empty()) return false;
        size_t at = 0;
        const strata::TensorInfo* tensor = model.find("output.weight", &at);
        const strata::GgufFile& gguf = model.shard(at);
        if (!tensor || tensor->type != 13 || tensor->shape.size() != 2 || tensor->shape[0] != (uint64_t) n_in ||
            tensor->shape[1] != (uint64_t) n_out) {
            err = "split native head currently requires the canonical Q5_K output.weight";
            return false;
        }
        const uint64_t full_bytes = strata::kernels::native_mmvq_weight_bytes(13, n_in, n_out);
        const size_t row_bytes = (size_t) (full_bytes / (uint64_t) n_out);
        const int rows0 = (int) (n_out / 2), rows1 = (int) (n_out - rows0);
        const uint64_t bytes0 = (uint64_t) rows0 * row_bytes;
        const uint64_t bytes1 = full_bytes - bytes0;
        const uint64_t payload = gguf.file_size() - gguf.data_start();
        if (tensor->offset > payload || full_bytes > payload - tensor->offset) {
            err = "split native head: truncated output.weight payload";
            return false;
        }
        void* weights0 = nullptr;
        void* weights1 = nullptr;
        void* scratch0 = nullptr;
        cudaError_t status = cudaMalloc(&weights0, bytes0);
        if (status == cudaSuccess)
            status = cudaMalloc(&scratch0, strata::kernels::native_q8_1_bytes(n_in, 1));
        if (status == cudaSuccess)
            status = cudaMemcpy(weights0, gguf.tensor_data(*tensor), bytes0, cudaMemcpyHostToDevice);
        if (status == cudaSuccess) {
            int can = 0;
            cudaError_t access = cudaDeviceCanAccessPeer(&can, main_device, peer);
            if (access == cudaSuccess && can) access = cudaDeviceEnablePeerAccess(peer, 0);
            if (access == cudaErrorPeerAccessAlreadyEnabled) { (void) cudaGetLastError(); access = cudaSuccess; }
            if (access != cudaSuccess || !can) status = access == cudaSuccess ? cudaErrorPeerAccessUnsupported : access;
        }
        if (status == cudaSuccess) status = cudaSetDevice(peer);
        if (status == cudaSuccess) {
            int can = 0;
            status = cudaDeviceCanAccessPeer(&can, peer, main_device);
            if (status == cudaSuccess && can) status = cudaDeviceEnablePeerAccess(main_device, 0);
            if (status == cudaErrorPeerAccessAlreadyEnabled) { (void) cudaGetLastError(); status = cudaSuccess; }
            if (status == cudaSuccess && !can) status = cudaErrorPeerAccessUnsupported;
        }
        if (status == cudaSuccess) status = cudaMalloc(&weights1, bytes1);
        if (status == cudaSuccess)
            status = cudaMemcpy(weights1, gguf.tensor_data(*tensor) + bytes0, bytes1, cudaMemcpyHostToDevice);
        if (status == cudaSuccess) status = cudaSetDevice(main_device);
        if (status != cudaSuccess) {
            cudaSetDevice(peer);
            if (weights1) cudaFree(weights1);
            cudaSetDevice(main_device);
            if (scratch0) cudaFree(scratch0);
            if (weights0) cudaFree(weights0);
            err = std::string("split native head upload: ") + cudaGetErrorString(status);
            return false;
        }
        strata::platform::advise_willneed(gguf.tensor_data(*tensor), full_bytes);
        weights_ = weights0;
        peer_weights_ = weights1;
        scratch_ = scratch0;
        bytes_ = full_bytes;
        n_in_ = (int) n_in;
        n_out_ = (int) n_out;
        type_ = 13;
        device_ = main_device;
        peer_device_ = peer;
        split_rows0_ = rows0;
        split_rows1_ = rows1;
        std::fprintf(stderr, "strata: native head split Q5_K %d->%d as %d+%d rows on CUDA%d/CUDA%d\n",
                     n_in_, n_out_, rows0, rows1, main_device, peer);
        return true;
    } catch (const std::exception& error) {
        err = std::string("split native head: ") + error.what();
        return false;
    }
}

bool NativeHead::run(const float* mixed, float* logits, void* stream, std::string& err) const {
    if (!loaded() || split() || !mixed || !logits || !stream) {
        err = "native head requires loaded weights, device buffers and an explicit stream";
        return false;
    }
    try {
        if (type_ == 13) {
            strata::kernels::native_q5_k_f32(weights_, mixed, scratch_, logits, n_in_, n_out_, 1, stream);
        } else {
            strata::kernels::native_quantize_q8_1(mixed, scratch_, n_in_, 1, stream);
            strata::kernels::native_mmvq(type_, weights_, scratch_, logits, n_in_, n_out_, 1, stream);
        }
    } catch (const std::exception& error) {
        err = std::string("native head launch: ") + error.what();
        return false;
    }
    const cudaError_t status = cudaPeekAtLastError();
    if (status != cudaSuccess) {
        err = std::string("native head launch: ") + cudaGetErrorString(status);
        return false;
    }
    return true;
}

bool NativeHead::gather_rows_device(const int32_t* ids, int64_t n, uint8_t* dst, void* stream, std::string& err) const {
    if (!loaded() || !ids || n < 0 || !dst || !stream) {
        err = "native head row gather requires ids, a destination and a stream";
        return false;
    }
    try {
        if (split())
            strata::kernels::gather_rows_split((const uint8_t*) weights_, split_rows0_,
                                               (const uint8_t*) peer_weights_, row_bytes(), ids, n, dst, stream);
        else
            strata::kernels::gather_rows((const uint8_t*) weights_, row_bytes(), ids, n, dst, stream);
    } catch (const std::exception& error) {
        err = std::string("native head row gather: ") + error.what();
        return false;
    }
    const cudaError_t status = cudaPeekAtLastError();
    if (status != cudaSuccess) {
        err = std::string("native head row gather: ") + cudaGetErrorString(status);
        return false;
    }
    return true;
}

// ================================ plan v0.3 P6: THE NATIVE EMBEDDING ================================

namespace {
const NativeEmbed* g_embed = nullptr;
}
void set_native_embed(const NativeEmbed* e) { g_embed = e; }
const NativeEmbed* native_embed() { return g_embed; }

NativeEmbed::~NativeEmbed() {
    if (host_) cudaFreeHost(host_);
    else if (dev_) cudaFree(const_cast<void*>(dev_));   // the VRAM fallback below
}

bool NativeEmbed::load(const std::vector<std::string>& shards, int64_t n_embd, int64_t n_vocab, std::string& err) {
    try {
        const strata::GgufModel model(shards);
        // --embd-gguf's one-tensor file (tools/embd_bf16_pack.py) says "strata-embd": only its tensor is checked
        const strata::MetaValue* arch = model.meta().get("general.architecture");
        err = arch != nullptr && arch->s == "strata-embd" ? std::string() : strata::check_engine_architecture(model.meta());
        if (!err.empty()) { err = "native embedding: " + err; return false; }
        size_t at = 0;
        const strata::TensorInfo* t = model.find("token_embd.weight", &at);
        const strata::GgufFile& gguf = model.shard(at);
        if (!t || t->shape.size() != 2 || t->shape[0] != (uint64_t) n_embd || t->shape[1] != (uint64_t) n_vocab ||
            !strata::kernels::embed_type_supported((int) t->type) || n_embd % 256) {
            err = "native embedding: token_embd.weight is absent, of another shape, or of a type without a GPU "
                  "dequantizer";
            return false;
        }
        row_ = strata::kernels::iq_row_bytes((int) t->type, n_embd);
        bytes_ = (uint64_t) row_ * (uint64_t) n_vocab;
        // the table is copied out of the mapping below: a truncated shard must be an error, not a read past EOF
        if (!model.in_bounds(*t, at) || strata::tensor_payload_bytes(*t) != bytes_) {
            err = "native embedding: token_embd.weight's payload is truncated or not " + std::to_string(bytes_) +
                  " B (" + gguf.path() + ")";
            bytes_ = 0;
            return false;
        }
        strata::platform::advise_willneed(gguf.tensor_data(*t), bytes_);   // copied out of the mapping below
        cudaError_t host_err = cudaHostAlloc(&host_, bytes_, cudaHostAllocMapped | cudaHostAllocPortable);
        if (host_err != cudaSuccess) {
            // Under WSL2 the driver's pinned/mapped host budget (~1 GiB) can be spent by the GPU contexts
            // themselves (three cards). The table is only gathered from, so keep it in the current device's VRAM
            // instead: it costs its size there and reads faster than over PCIe.
            cudaGetLastError();
            host_ = nullptr;
            void* d = nullptr;
            if (cudaMalloc(&d, bytes_) != cudaSuccess ||
                cudaMemcpy(d, gguf.tensor_data(*t), bytes_, cudaMemcpyHostToDevice) != cudaSuccess) {
                if (d) cudaFree(d);
                cudaGetLastError();
                err = "native embedding: cannot pin " + std::to_string(bytes_ >> 20) + " MiB: " +
                      cudaGetErrorString(host_err) + ", nor place it in VRAM";
                return false;
            }
            std::fprintf(stderr, "strata: native embedding: cannot pin %llu MiB, kept in VRAM instead\n",
                         (unsigned long long) (bytes_ >> 20));
            dev_ = d;
        } else {
            std::memcpy(host_, gguf.tensor_data(*t), bytes_);
            void* d = nullptr;
            if (cudaHostGetDevicePointer(&d, host_, 0) != cudaSuccess) {
                err = "native embedding: no device alias for the mapped table";
                return false;
            }
            dev_ = d;
        }
        type_ = (int) t->type;
        n_embd_ = n_embd;
        n_vocab_ = n_vocab;
        return true;
    } catch (const std::exception& e) {
        err = std::string("native embedding: ") + e.what();
        return false;
    }
}

void NativeEmbed::gather_dev(const int32_t* tokens, int64_t n_tok, float* out, void* stream) const {
    strata::kernels::iq_embed_rows(type_, dev_, row_, tokens, n_tok, n_embd_, out, stream);
}

void NativeEmbed::gather_one(int64_t token, float* out, void* stream) const {
    strata::kernels::iq_dequant_f32(type_, (const uint8_t*) dev_ + (size_t) token * row_, n_embd_, out, stream);
}

}  // namespace strata::core
