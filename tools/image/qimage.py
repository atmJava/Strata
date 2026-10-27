"""tools/image/qimage.py - Qwen-Image-2.1-Turbo for Strata's image endpoint: stable-diffusion.cpp (sd-cli) at a pinned
commit, built for Linux CUDA sm_75, and the model's three files at pinned Hugging Face revisions.

tools/image/install.py uses these pieces; serve/images.py runs the result.

  backend   Linux CUDA sm_75 only.
  files     the denoiser (DiT) quant by the server GPU's memory: Q4_K_M up to 8 GB, Q6_K up to 12 GB, Q8_0 above (Unsloth's
            LPIPS vs bf16: 0.153 / 0.055 / 0.036); the VAE (bf16); the Qwen3-VL-8B text encoder UD-Q4_K_XL (the
            model card's choice).  Each file is checked against the SHA-256 the Hugging Face API lists for it at the
            pinned revision.
  defaults  8 steps, CFG 1, euler, Turbo's own sigmas (serve/images.py), --diffusion-fa.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

SD_CPP_COMMIT = "2988060a4338a5dcd94721c964a8e87003c1ac8b"     # "add TAEQI2.1 support for Qwen Image 2.1" (#2123)
SD_GGML_COMMIT = "d25b121ce1d6ae0c7e62ad080460d9fc1e46dd93"    # its ggml submodule (leejet/ggml)
SD_CPP_ZIP = f"https://github.com/leejet/stable-diffusion.cpp/archive/{SD_CPP_COMMIT}.zip"
SD_GGML_ZIP = f"https://github.com/leejet/ggml/archive/{SD_GGML_COMMIT}.zip"

REVISIONS = {                                       # the `sha` of https://huggingface.co/api/models/<repo>, 2026-10-12
    "unsloth/Qwen-Image-2.1-Turbo-GGUF": "5d1f9f759ea877ae556cda88dbec958b62ad3809",
    "unsloth/Qwen-Image-2.1-FP8": "bb21a8ba7f0371c19ad1f72c82a5e8fc10f39939",
    "unsloth/Qwen3-VL-8B-Instruct-GGUF": "b93a7ee713758252c555be4210c00540df954dc2",
}
_DIT = "unsloth/Qwen-Image-2.1-Turbo-GGUF"
# name -> (repo, path in the repo, bytes, sha256): the Hub's LFS pointers at the pinned revisions
FILES = {
    "Q4_K_M": (_DIT, "qwen-image-2.1-turbo-Q4_K_M.gguf", 4_199_565_120,
               "8940e8b522af886e0c0f5afddf720e82dd223cb9a43580b5f61c4d740452ac20"),
    "Q6_K": (_DIT, "qwen-image-2.1-turbo-Q6_K.gguf", 6_271_551_296,
             "f4560fb47ad863ffed7e32737d3c42988c44bd0c459606777d7ffe2e895d0a70"),
    "Q8_0": (_DIT, "qwen-image-2.1-turbo-Q8_0.gguf", 7_640_860_480,
             "399d2ddc3636f36456e2f5bfa57187da4e38721bc046430317f42a9eab5b1712"),
    "vae": ("unsloth/Qwen-Image-2.1-FP8", "vae/qwen_image_2.1_vae_bf16.safetensors", 675_508_656,
            "71879ffd5321e6d10c3c87513e2b474b1252efa7f3dec2969214a9bf06a6dd5c"),
    "llm": ("unsloth/Qwen3-VL-8B-Instruct-GGUF", "Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf", 5_148_699_488,
            "e3d1a6e87c5cb31e054f2c3bc0dd82ffde052f613de5eef3665b7bd33c9b703e"),
}
QUANTS = ("Q4_K_M", "Q6_K", "Q8_0")
BACKENDS = ("cuda",)
LICENSE_NOTE = ("Qwen-Image-2.1-Turbo is under the Qwen Research License (not Apache 2.0): "
                "https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo/blob/main/LICENSE - read it before you use the images "
                "commercially")
MODEL_DIR = "qwen-image-2.1-turbo"
SD_EXE = "sd-cli"


def pick_quant(gpu_gb: float) -> str:
    """The denoiser for a GPU with `gpu_gb` of memory: Q4_K_M up to 8 GB, Q6_K up to 12 GB, Q8_0 above."""
    if gpu_gb <= 8:
        return "Q4_K_M"
    if gpu_gb <= 12:
        return "Q6_K"
    return "Q8_0"


def default_args(backend: str) -> list[str]:
    """sd-cli's fixed arguments besides the ones serve/images.py writes per request."""
    if backend != "cuda":
        raise ValueError(f"image generation supports CUDA only, not {backend!r}")
    return ["--diffusion-fa"]


def file_url(name: str, endpoint: str = "https://huggingface.co") -> str:
    repo, path, _, _ = FILES[name]
    return f"{endpoint.rstrip('/')}/{repo}/resolve/{REVISIONS[repo]}/{path}"


def local_path(models_dir: Path, name: str) -> Path:
    return Path(models_dir) / MODEL_DIR / Path(FILES[name][1]).name


def cmake_defs(backend: str, arch=None, cuda_root: Path | None = None) -> list[str]:
    """CMake definitions for the server's CUDA sm_75 sd-cli build."""
    if backend != "cuda":
        raise ValueError(f"image generation supports CUDA only, not {backend!r}")
    if arch not in (None, 75, "75"):
        raise ValueError(f"image generation supports CUDA sm_75 only, not {arch!r}")
    defs = ["-DSD_BUILD_EXAMPLES=ON", "-DSD_WEBP=OFF", "-DSD_WEBM=OFF", "-DSD_BUILD_SHARED_LIBS=OFF",
            "-DGGML_NATIVE=ON", "-DSD_CUDA=ON", "-DCMAKE_CUDA_ARCHITECTURES=75"]
    if cuda_root:
        defs += [f"-DCUDAToolkit_ROOT={cuda_root}"]
    return defs


def unpack_source(third_party: Path, sd_zip: Path, ggml_zip: Path) -> Path:
    """stable-diffusion.cpp's zip with its ggml submodule's zip inside (GitHub's archives leave submodules out) ->
    third_party/stable-diffusion.cpp, stamped with the two commits."""
    dst = Path(third_party) / "stable-diffusion.cpp"
    tmp = Path(third_party) / "_unpack-sd"
    shutil.rmtree(tmp, ignore_errors=True)
    for z, sub in ((sd_zip, "sd"), (ggml_zip, "ggml")):
        with zipfile.ZipFile(z) as f:
            f.extractall(tmp / sub)
    top = next((tmp / "sd").iterdir())
    gtop = next((tmp / "ggml").iterdir())
    shutil.rmtree(top / "ggml", ignore_errors=True)
    shutil.move(str(gtop), str(top / "ggml"))
    shutil.rmtree(dst, ignore_errors=True)
    shutil.move(str(top), str(dst))
    shutil.rmtree(tmp, ignore_errors=True)
    (dst / ".strata-pin").write_text(f"{SD_CPP_COMMIT}\n{SD_GGML_COMMIT}\n", encoding="utf-8")
    return dst


def source_ok(src: Path) -> bool:
    pin = Path(src) / ".strata-pin"
    return pin.is_file() and pin.read_text(encoding="utf-8").split() == [SD_CPP_COMMIT, SD_GGML_COMMIT] \
        and (Path(src) / "ggml" / "CMakeLists.txt").is_file()


NATIVE_DIR = Path(__file__).resolve().parent / "sdcpp_native"
def native_hash() -> str:
    """A fingerprint of the native runner's sources (a change compiles sd-cli again)."""
    import hashlib
    h = hashlib.sha256()
    for f in sorted(NATIVE_DIR.glob("*")):
        if f.is_file() and f.suffix in (".cu", ".hpp", ".py"):
            h.update(f.name.encode() + b"\0" + f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:16]


def build_stamp(backend: str, arch) -> dict:
    stamp = {"sd_cpp": SD_CPP_COMMIT, "ggml": SD_GGML_COMMIT, "backend": backend, "arch": str(arch or "")}
    if backend == "cuda":
        stamp["native"] = native_hash()
    return stamp


def image_config(backend: str, quant: str, exe: Path, models_dir: Path, *, gpu=None,
                 lib_dirs=(), policy: str = "auto", native: bool = False) -> dict:
    """The run config's "images" section (serve/images.py reads it)."""
    if backend != "cuda":
        raise ValueError(f"image generation supports CUDA only, not {backend!r}")
    cfg = {"model_name": "qwen-image-2.1-turbo", "exe": str(exe), "backend": backend, "quant": quant,
           "diffusion_model": str(local_path(models_dir, quant)), "vae": str(local_path(models_dir, "vae")),
           "llm": str(local_path(models_dir, "llm")), "args": default_args(backend), "gpu_policy": policy,
           "sd_cpp": SD_CPP_COMMIT[:7]}
    cfg["native"] = bool(native)           # Strata's DiT runner (--model-args qwen_image_2_1_native=1)
    if gpu is not None:
        cfg["gpu"] = gpu
    if lib_dirs:
        cfg["lib_dirs"] = [str(d) for d in lib_dirs]
    return cfg
