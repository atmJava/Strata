import importlib.util
import json
import pathlib
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "bench-hot-turn-fixed-cost.py"
SPEC = importlib.util.spec_from_file_location("bench_hot_turn_fixed_cost", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def arm(
    name: str,
    prompt_ms: float,
    wall_s: float,
    *,
    waves: int = 3,
    cold_wall_s: float | None = None,
) -> dict:
    requests = []
    for wave in range(waves):
        for turn in ("cold", "hot_short", "hot_long"):
            requests.append(
                {
                    "level": 1,
                    "turn": turn,
                    "wave": wave,
                    "prompt_ms": prompt_ms,
                    "wall_s": (
                        cold_wall_s
                        if turn == "cold" and cold_wall_s is not None
                        else wall_s
                    ),
                    "error": None,
                }
            )
    summary = {
        "1": {
            turn: {
                "errors": 0,
                "finish": ["stop"],
                "replies_expected": waves,
                "requests": waves,
            }
            for turn in ("cold", "hot_short", "hot_long")
        }
    }
    return {
        "arm": name,
        "args": {"waves": waves},
        "request_accounting": {"foreign": 0},
        "summary": summary,
        "requests": requests,
    }


class BenchHotTurnFixedCostCompareTest(unittest.TestCase):
    def _write(self, root: pathlib.Path, name: str, payload: dict) -> pathlib.Path:
        path = root / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_compare_accepts_contract_specific_arm_names_and_thresholds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            paths = [
                self._write(root, "base", arm("B0", 400.0, 1.000)),
                self._write(root, "candidate", arm("B1", 380.0, 0.980)),
                self._write(root, "control", arm("B0r", 404.0, 1.010)),
            ]
            verdict, problems = MODULE.compare(
                paths,
                baseline_arm="B0",
                candidate_arm="B1",
                control_arm="B0r",
                min_gain_pct=1.0,
                min_wall_s=0.015,
            )
            self.assertEqual(problems, [])
            self.assertTrue(verdict["pass"])
            row = verdict["comparison"]["1"]["hot_long"]["wall_s"]
            self.assertEqual(set(row["arms"]), {"B0", "B1", "B0r"})
            self.assertEqual(row["candidate_wins"], 3)

    def test_compare_enforces_configured_absolute_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            paths = [
                self._write(root, "base", arm("B0", 400.0, 1.000)),
                self._write(root, "candidate", arm("B1", 380.0, 0.990)),
                self._write(root, "control", arm("B0r", 400.0, 1.000)),
            ]
            _, problems = MODULE.compare(
                paths,
                baseline_arm="B0",
                candidate_arm="B1",
                control_arm="B0r",
                min_gain_pct=1.0,
                min_wall_s=0.015,
            )
            self.assertTrue(
                any("saved at least 0.015 s" in problem for problem in problems),
                problems,
            )

    def test_compare_rejects_duplicate_arm_names(self):
        verdict, problems = MODULE.compare(
            [],
            baseline_arm="B0",
            candidate_arm="B0",
            control_arm="B0r",
        )
        self.assertEqual(verdict, {})
        self.assertEqual(
            problems,
            ["baseline, candidate and control arm names must be distinct"],
        )

    def test_compare_requires_every_recorded_wave(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            baseline = arm("B0", 400.0, 1.000)
            candidate = arm("B1", 380.0, 0.980)
            control = arm("B0r", 404.0, 1.010)
            for payload in (baseline, candidate, control):
                payload["requests"] = [
                    row for row in payload["requests"]
                    if not (row["wave"] == 2 and row["turn"] == "hot_long")
                ]
            paths = [
                self._write(root, "base", baseline),
                self._write(root, "candidate", candidate),
                self._write(root, "control", control),
            ]
            _, problems = MODULE.compare(
                paths,
                baseline_arm="B0",
                candidate_arm="B1",
                control_arm="B0r",
                min_gain_pct=1.0,
                min_wall_s=0.015,
            )
            self.assertTrue(
                any("2/3 complete waves" in problem for problem in problems),
                problems,
            )

    def test_compare_rejects_cold_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            paths = [
                self._write(
                    root,
                    "base",
                    arm("B0", 400.0, 1.000, cold_wall_s=10.000),
                ),
                self._write(
                    root,
                    "candidate",
                    arm(
                        "B1",
                        380.0,
                        0.980,
                        cold_wall_s=10.200,
                    ),
                ),
                self._write(
                    root,
                    "control",
                    arm("B0r", 400.0, 1.000, cold_wall_s=10.000),
                ),
            ]
            _, problems = MODULE.compare(
                paths,
                baseline_arm="B0",
                candidate_arm="B1",
                control_arm="B0r",
                min_gain_pct=1.0,
                min_wall_s=0.015,
            )
            self.assertTrue(
                any(
                    "level 1 cold wall_s: regressed" in problem
                    for problem in problems
                ),
                problems,
            )

    def test_compare_requires_two_thirds_of_more_than_three_waves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            paths = [
                self._write(root, "base", arm("B0", 400.0, 1.000, waves=6)),
                self._write(root, "candidate", arm("B1", 380.0, 0.980, waves=6)),
                self._write(root, "control", arm("B0r", 400.0, 1.000, waves=6)),
            ]
            candidate = json.loads(paths[1].read_text(encoding="utf-8"))
            for row in candidate["requests"]:
                if row["turn"] in ("hot_short", "hot_long") and row["wave"] >= 3:
                    row["wall_s"] = 1.300
                    row["prompt_ms"] = 450.0
            paths[1].write_text(json.dumps(candidate), encoding="utf-8")
            _, problems = MODULE.compare(
                paths,
                baseline_arm="B0",
                candidate_arm="B1",
                control_arm="B0r",
                min_gain_pct=1.0,
                min_wall_s=0.015,
            )
            self.assertTrue(
                any(
                    "4/6 waves" in problem
                    for problem in problems
                ),
                problems,
            )


if __name__ == "__main__":
    unittest.main()
