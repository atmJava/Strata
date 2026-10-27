from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from strata_control import drafting

MODEL = "qwen3.8-flash-next-iq3_xxs"


class Drafting(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="strata-drafting-test-")
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)

    def ready_pack(self) -> Path:
        path = drafting.pack_path(self.data)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"pack" * (1 << 19))
        return path

    def test_dflash2_is_retained_and_explicit_selection_adds_engine_flags(self):
        pack = self.ready_pack()
        decision = drafting.decide(
            "dflash2", klass="sm75", model_name=MODEL, data_dir=self.data,
        )
        self.assertEqual(decision.drafter, "dflash2")
        self.assertEqual(decision.flags, (
            "--draft", "dflash2", "--draft-model", str(pack),
        ))

    def test_auto_stays_on_mtp_and_missing_pack_falls_back(self):
        self.assertEqual(drafting.suggest("sm75"), "mtp")
        decision = drafting.decide(
            "dflash2", klass="sm75", model_name=MODEL, data_dir=self.data,
        )
        self.assertEqual(
            (decision.drafter, decision.note),
            ("mtp", drafting.NOT_DOWNLOADED),
        )

    def test_dflash2_refuses_other_models(self):
        self.ready_pack()
        decision = drafting.decide(
            "dflash2", klass="sm75",
            model_name="qwen3.6-35b-a3b-q4", data_dir=self.data,
        )
        self.assertEqual(
            (decision.drafter, decision.note),
            ("mtp", drafting.NOT_SUPPORTED),
        )

    def test_engine_log_reports_actual_drafter_and_fallback(self):
        args = ["--mtp", "/model/mtp", "--draft", "dflash2", "--draft-model", "/model/pack"]
        self.assertEqual(
            drafting.engine_info(args, "strata: DFlash2 drafter on (/model/pack)"),
            {"drafting": "dflash2", "drafting_note": ""},
        )
        self.assertEqual(
            drafting.engine_info(args, "--draft dflash2: could not allocate the drafter"),
            {
                "drafting": "mtp",
                "drafting_note": drafting.mtp_line(drafting.DIDNT_FIT),
            },
        )

    def test_data_dir_accepts_both_mtp_shapes_and_uses_the_config_cwd(self):
        self.assertEqual(
            drafting.data_dir_of(["--mtp", "/model/mtp"]),
            Path("/model"),
        )
        self.assertEqual(
            drafting.data_dir_of(["--mtp", "/model/mtp/rt"]),
            Path("/model"),
        )
        self.assertEqual(
            drafting.data_dir_of(["--pack", "/model/packs/iq3_xxs"]),
            Path("/model"),
        )
        self.assertEqual(
            drafting.data_dir_of(["--mtp", "mtp/rt"], cwd="/models/qwen"),
            Path("/models/qwen"),
        )
        self.assertIsNone(drafting.data_dir_of(["--mtp", "4"]))
        self.assertIsNone(drafting.data_dir_of([]))


if __name__ == "__main__":
    unittest.main()
