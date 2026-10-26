"""Tests for the CUDA sm_75 image-generation installer.

    python -m unittest tools.test_qimage
"""
from __future__ import annotations

import json
import contextlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "image"))
import install as INSTALL  # noqa: E402
import qimage as QI  # noqa: E402


class Pins(unittest.TestCase):
    def test_shapes(self):
        for commit in (QI.SD_CPP_COMMIT, QI.SD_GGML_COMMIT, *QI.REVISIONS.values()):
            self.assertRegex(commit, r"^[0-9a-f]{40}$")
        for name, (repo, _, size, digest) in QI.FILES.items():
            self.assertIn(repo, QI.REVISIONS, name)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertGreater(size, 100 << 20)
        self.assertEqual(
            QI.file_url("Q8_0"),
            "https://huggingface.co/unsloth/Qwen-Image-2.1-Turbo-GGUF/resolve/"
            "5d1f9f759ea877ae556cda88dbec958b62ad3809/qwen-image-2.1-turbo-Q8_0.gguf",
        )
        self.assertEqual(
            QI.local_path(Path("m"), "vae"),
            Path("m") / "qwen-image-2.1-turbo" / "qwen_image_2.1_vae_bf16.safetensors",
        )

    def test_sigmas_are_the_models(self):
        from serve import images

        sample = [1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568]
        self.assertEqual([float(value) for value in images.TURBO_SIGMAS.split(",")], sample + [0.0])


class Choices(unittest.TestCase):
    def test_quant_by_memory(self):
        self.assertEqual(
            [QI.pick_quant(value) for value in (4, 8, 8.5, 12, 12.1, 22, 32)],
            ["Q4_K_M", "Q4_K_M", "Q6_K", "Q6_K", "Q8_0", "Q8_0", "Q8_0"],
        )

    def test_only_cuda_sm75_is_accepted(self):
        self.assertEqual(QI.BACKENDS, ("cuda",))
        self.assertEqual(QI.default_args("cuda"), ["--diffusion-fa"])
        for backend in ("hip", "vulkan", "cpu", "metal"):
            with self.subTest(backend=backend), self.assertRaises(ValueError):
                QI.default_args(backend)

    def test_config_section(self):
        section = QI.image_config("cuda", "Q8_0", Path("/e/sd-cli"), Path("/m"), gpu=1, lib_dirs=["/l"])
        self.assertEqual(
            section["diffusion_model"],
            "/m/qwen-image-2.1-turbo/qwen-image-2.1-turbo-Q8_0.gguf",
        )
        self.assertEqual(section["args"], ["--diffusion-fa"])
        self.assertEqual((section["backend"], section["gpu"], section["gpu_policy"]), ("cuda", 1, "auto"))
        from serve.images import ImageGen

        with tempfile.TemporaryDirectory() as directory:
            ImageGen({**section, "out_dir": directory})
        with self.assertRaises(ValueError):
            QI.image_config("hip", "Q8_0", Path("/e/sd-cli"), Path("/m"))


class Build(unittest.TestCase):
    def test_cmake_defs_are_fixed_to_cuda_sm75(self):
        definitions = QI.cmake_defs("cuda", arch=75, cuda_root=Path("/usr/local/cuda"))
        self.assertIn("-DSD_CUDA=ON", definitions)
        self.assertIn("-DCMAKE_CUDA_ARCHITECTURES=75", definitions)
        self.assertIn("-DCUDAToolkit_ROOT=/usr/local/cuda", definitions)
        self.assertIn("-DSD_WEBP=OFF", definitions)
        for backend in ("hip", "vulkan", "cpu"):
            with self.subTest(backend=backend), self.assertRaises(ValueError):
                QI.cmake_defs(backend)
        with self.assertRaises(ValueError):
            QI.cmake_defs("cuda", arch=86)

    def test_unpack_puts_ggml_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            sd_zip, ggml_zip = directory / "sd.zip", directory / "ggml.zip"
            with zipfile.ZipFile(sd_zip, "w") as archive:
                archive.writestr(f"stable-diffusion.cpp-{QI.SD_CPP_COMMIT}/CMakeLists.txt", "project(sd)")
                archive.writestr(f"stable-diffusion.cpp-{QI.SD_CPP_COMMIT}/ggml/", "")
            with zipfile.ZipFile(ggml_zip, "w") as archive:
                archive.writestr(f"ggml-{QI.SD_GGML_COMMIT}/CMakeLists.txt", "project(ggml)")
            source = QI.unpack_source(directory, sd_zip, ggml_zip)
            self.assertEqual((source / "ggml" / "CMakeLists.txt").read_text(), "project(ggml)")
            self.assertTrue(QI.source_ok(source))
            (source / ".strata-pin").write_text("0" * 40 + "\n" + QI.SD_GGML_COMMIT + "\n")
            self.assertFalse(QI.source_ok(source))

    def test_build_stamp_always_contains_native_cuda_runner(self):
        self.assertIn("native", QI.build_stamp("cuda", 75))


class Install(unittest.TestCase):
    def test_print_uses_cuda_section(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "sd-cli"
            with mock.patch.object(INSTALL, "build_sd_cli", return_value=(executable, [])), \
                    mock.patch.object(INSTALL, "download") as download, \
                    mock.patch.object(INSTALL, "verify_sha256") as verify, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(INSTALL.main(["--print", "--gpu-gb", "22", "--models-dir", directory]), 0)
            self.assertEqual(download.call_count, 3)
            self.assertEqual(verify.call_count, 3)
            self.assertIn('"backend": "cuda"', output.getvalue())

    def test_write_config_replaces_only_the_images_section(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(json.dumps({"backend": "cuda", "arch": "qwen4exp", "keep": 1}))
            executable = Path(directory) / "sd-cli"
            with mock.patch.object(INSTALL, "build_sd_cli", return_value=(executable, [])), \
                    mock.patch.object(INSTALL, "download"), \
                    mock.patch.object(INSTALL, "verify_sha256"):
                self.assertEqual(
                    INSTALL.main(["--config", str(config), "--gpu-gb", "22", "--models-dir", directory]),
                    0,
                )
            result = json.loads(config.read_text())
            self.assertEqual((result["backend"], result["arch"], result["keep"]), ("cuda", "qwen4exp", 1))
            self.assertEqual(result["images"]["backend"], "cuda")

    def test_native_patch_is_idempotent(self):
        sys.path.insert(0, str(QI.NATIVE_DIR))
        import apply as native_apply

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "ggml" / "src" / "ggml-cuda").mkdir(parents=True)
            (source / "src" / "model" / "diffusion").mkdir(parents=True)
            (source / "src" / "pipeline").mkdir(parents=True)
            (source / "src" / "pipeline" / "model_builders.cpp").write_text(
                "#include <x>\n" + native_apply.INCLUDE_OLD + "int a;\n" + native_apply.BUILDER_OLD + "            }\n",
                encoding="utf-8",
            )
            (source / "CMakeLists.txt").write_text("x\n" + native_apply.CMAKE_OLD + "y\n", encoding="utf-8")
            native_apply.apply(source)
            first = [
                (source / name).read_text(encoding="utf-8")
                for name in ("src/pipeline/model_builders.cpp", "CMakeLists.txt")
            ]
            native_apply.apply(source)
            second = [
                (source / name).read_text(encoding="utf-8")
                for name in ("src/pipeline/model_builders.cpp", "CMakeLists.txt")
            ]
            self.assertEqual(first, second)
            self.assertIn("QwenImage21NativeRunner", first[0])
            self.assertTrue((source / "ggml/src/ggml-cuda/qi21-native.cu").is_file())


if __name__ == "__main__":
    unittest.main()
