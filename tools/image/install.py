"""Install Qwen-Image-2.1-Turbo for the Linux CUDA sm_75 server.

Builds the pinned stable-diffusion.cpp ``sd-cli`` for CUDA sm_75, downloads and verifies the three model
files, and writes the ``images`` section of a run config.  This script is standalone; source setup and setup.py
are not part of this distribution.

    python tools/image/install.py --config strata-production.json
    python tools/image/install.py --build-only
    python tools/image/install.py --print --gpu-gb 22 --quant Q6_K
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "image"))
import qimage as QI  # noqa: E402

IMAGE_ENGINE = ROOT / "engine-image"
DEFAULT_DATA = Path(os.environ.get("STRATA_DATA_DIR", Path.home() / ".local" / "share" / "strata"))


def say(message: str) -> None:
    print(f"[images] {message}", flush=True)


def download(url: str, destination: Path, description: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    say(f"downloading {description}")
    try:
        with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output, length=8 << 20)
        partial.replace(destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def verify_sha256(path: Path, size: int, expected: str) -> None:
    actual_size = path.stat().st_size
    if actual_size != size:
        raise RuntimeError(f"{path}: expected {size} bytes, found {actual_size}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise RuntimeError(f"{path}: SHA-256 mismatch (expected {expected}, found {digest.hexdigest()})")


def source_tree() -> Path:
    source = ROOT / "third_party" / "stable-diffusion.cpp"
    if QI.source_ok(source):
        return source
    third_party = ROOT / "third_party"
    third_party.mkdir(parents=True, exist_ok=True)
    sd_zip = third_party / f"stable-diffusion.cpp-{QI.SD_CPP_COMMIT[:7]}.zip"
    ggml_zip = third_party / f"sd-ggml-{QI.SD_GGML_COMMIT[:7]}.zip"
    download(QI.SD_CPP_ZIP, sd_zip, "stable-diffusion.cpp source")
    download(QI.SD_GGML_ZIP, ggml_zip, "stable-diffusion.cpp's ggml source")
    try:
        return QI.unpack_source(third_party, sd_zip, ggml_zip)
    finally:
        sd_zip.unlink(missing_ok=True)
        ggml_zip.unlink(missing_ok=True)


def apply_native_runner(source: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("strata_sdcpp_native_apply", QI.NATIVE_DIR / "apply.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load tools/image/sdcpp_native/apply.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.apply(source)


def run(command: list[str], description: str) -> None:
    say(description)
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"{description} failed with exit code {completed.returncode}")


def build_sd_cli() -> tuple[Path, list[str]]:
    nvcc = shutil.which("nvcc")
    if nvcc is None:
        raise RuntimeError("CUDA sm_75 image generation needs nvcc on PATH")
    cuda_root = Path(nvcc).resolve().parent.parent
    source = source_tree()
    apply_native_runner(source)
    build = ROOT / "build-image-cuda"
    definitions = QI.cmake_defs("cuda", arch=75, cuda_root=cuda_root) + [
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DCMAKE_CUDA_COMPILER={nvcc}",
    ]
    run(["cmake", "-S", str(source), "-B", str(build), *definitions], "configuring sd-cli for CUDA sm_75")
    run(["cmake", "--build", str(build), "--target", "sd-cli", "--parallel"], "building sd-cli")
    built = build / "bin" / QI.SD_EXE
    if not built.is_file():
        raise RuntimeError(f"the build finished without {built}")
    IMAGE_ENGINE.mkdir(parents=True, exist_ok=True)
    executable = IMAGE_ENGINE / QI.SD_EXE
    shutil.copy2(built, executable)
    lib_dirs = [str(path) for path in (cuda_root / "lib64", cuda_root / "lib") if path.is_dir()]
    stamp = {**QI.build_stamp("cuda", 75), "lib_dirs": lib_dirs}
    (IMAGE_ENGINE / "BUILD.json").write_text(json.dumps(stamp, indent=1) + "\n", encoding="utf-8")
    return executable, lib_dirs


def detect_gpu_gb() -> float:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        sizes = [float(line.strip()) / 1024 for line in result.stdout.splitlines() if line.strip()]
        if not sizes:
            raise RuntimeError("nvidia-smi found no CUDA GPU")
        return min(sizes)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError("cannot detect GPU memory; pass --gpu-gb") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend", choices=QI.BACKENDS, default="cuda")
    parser.add_argument("--arch", type=int, choices=(75,), default=75)
    parser.add_argument("--quant", choices=QI.QUANTS)
    parser.add_argument("--gpu-gb", type=float, help="memory used to select the quant (default: detect with nvidia-smi)")
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_DATA / "models")
    parser.add_argument("--config", type=Path, help="run config to update")
    parser.add_argument("--gpu-policy", choices=("auto", "keep", "shrink", "unload"), default="auto")
    parser.add_argument("--gpu", type=int, help="CUDA device used by the image process")
    parser.add_argument("--native", choices=("on", "off"), default="off")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--print", action="store_true", help="print the images section instead of writing it")
    args = parser.parse_args(argv)

    try:
        gpu_gb = args.gpu_gb if args.gpu_gb is not None else detect_gpu_gb()
        quant = args.quant or QI.pick_quant(gpu_gb)
        executable, lib_dirs = build_sd_cli()
        if args.build_only:
            say(f"sd-cli built for CUDA sm_75: {executable}")
            return 0
        models = args.models_dir.expanduser().resolve()
        say(QI.LICENSE_NOTE)
        for name in (quant, "vae", "llm"):
            _, relative, size, digest = QI.FILES[name]
            destination = QI.local_path(models, name)
            if not destination.is_file() or destination.stat().st_size != size:
                download(QI.file_url(name), destination, f"image model: {Path(relative).name}")
            verify_sha256(destination, size, digest)
        section = QI.image_config(
            "cuda",
            quant,
            executable,
            models,
            gpu=args.gpu,
            lib_dirs=lib_dirs,
            policy=args.gpu_policy,
            native=args.native == "on",
        )
        if args.print or args.config is None:
            print(json.dumps({"images": section}, indent=1))
            return 0
        config_path = args.config.expanduser().resolve()
        config = json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.is_file() else {}
        if not isinstance(config, dict):
            raise RuntimeError(f"{config_path} is not a JSON object")
        config["images"] = section
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=config_path.parent, prefix=config_path.name + ".", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(config, handle, indent=1)
            handle.write("\n")
        temporary.replace(config_path)
        say(f"{config_path}: image generation added ({quant}, CUDA sm_75)")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[images] error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
