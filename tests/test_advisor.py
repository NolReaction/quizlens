import ctypes
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import advisor


def advise(system, accelerator, ram, vram=None, models=(), **details):
    return advisor.recommend(dict(platform=system, accelerator=accelerator, ramGiB=ram, vramGiB=vram,
                                  **details), models)


class AdvisorTest(unittest.TestCase):
    def test_mac_memory_steps_and_files(self):
        self.assertIsNone(advise("macos", "apple", 4)["primary"])
        self.assertTrue(advise("macos", "apple", None)["needsHardware"])
        portable = advise("macos", "apple", 8)
        basic = advise("macos", "apple", 16)
        spacious = advise("macos", "apple", 24)
        self.assertEqual(portable["primary"]["id"], "qwen2.5-1.5b-instruct")
        self.assertEqual(basic["primary"]["id"], "qwen3-4b-instruct-2507")
        self.assertEqual(basic["alternative"]["id"], portable["primary"]["id"])
        self.assertEqual(spacious["alternative"]["id"], "qwen2.5-7b-instruct")
        for card in (portable["primary"], basic["primary"], spacious["alternative"]):
            self.assertEqual(card["quant"], "Q4_K_M")
            self.assertRegex(card["fileUrl"], r"/blob/[0-9a-f]{40}/")
            self.assertRegex(card["downloadUrl"], r"/resolve/[0-9a-f]{40}/")
            self.assertTrue(card["downloadUrl"].lower().endswith("q4_k_m.gguf?download=true"))

    def test_dedicated_gpu_and_cpu_use_separate_memory(self):
        powerful = advise("windows", "discrete", 32, 10)
        self.assertEqual(powerful["primary"]["id"], "qwen3-4b-instruct-2507")
        self.assertEqual(powerful["alternative"]["id"], "qwen2.5-7b-instruct")
        budget = advise("windows", "discrete", 16, 6)
        self.assertEqual(budget["alternative"]["id"], "qwen2.5-1.5b-instruct")
        for system, accelerator, ram, vram in (("windows", "cpu", 128, 64), ("linux", "discrete", 64, 2),
                                                 ("linux", "discrete", 8, 24), ("unknown", "unknown", 64, None)):
            self.assertEqual(advise(system, accelerator, ram, vram)["primary"]["id"], "qwen2.5-1.5b-instruct")
        self.assertTrue(advise("windows", "discrete", 16)["needsHardware"])
        self.assertIsNone(advise("macos", "apple", 8, 16)["profile"]["vramGiB"])
        self.assertIsNone(advise("macos", "unknown", 32, arch="x86_64")["primary"])
        self.assertTrue(advise("macos", "unknown", 32)["needsHardware"])

    def test_available_family_and_variant_matching(self):
        base = "qwen3-4b-instruct-2507"
        for alias in (base, f"qwen/{base}", f"{base}-Q4_K_M.gguf", f"{base}-GGUF:Q4_K_M",
                      f"mlx-community/{base}-4bit", f"{base}:2"):
            selected = advise("macos", "apple", 32, models=[base + "-thinking", alias])
            self.assertEqual(selected["primary"]["installedId"], alias)
            self.assertEqual(selected["installedMatch"], alias)
        for unrelated in (base + "-thinking", "qwen3-4b-thinking-2507", f"{base}-Q8_0.gguf",
                          f"my-{base}", "qwen2.5-vl-7b-instruct", base + "-coder", ""):
            self.assertIsNone(advise("macos", "apple", 24, models=[unrelated])["installedMatch"])

    def test_catalog_specs_are_consistent_with_the_distributed_text_files(self):
        result = advise("windows", "discrete", 32, 10)
        catalog = {card["id"]: card for card in result["catalog"]}
        sizes = [card["fileBytes"] for card in result["catalog"]]
        self.assertEqual(sizes, sorted(sizes))
        self.assertEqual(len(catalog), len(result["catalog"]))
        self.assertGreaterEqual(len(catalog), 10)
        for key in ("primary", "alternative"):
            card = result[key]
            self.assertEqual(card, catalog[card["id"]])
        for card in catalog.values():
            self.assertEqual(card["fileUrl"].replace("/blob/", "/resolve/") + "?download=true", card["downloadUrl"])
            self.assertTrue(card["fileUrl"].endswith("/" + card["filename"]))
            self.assertGreater(card["fileBytes"], 0)
            self.assertGreater(card["parametersB"], 0)
            self.assertTrue(card["sourceUrl"].startswith("https://huggingface.co/"))
            if card["maxOutputTokens"] is not None:
                self.assertLessEqual(card["maxOutputTokens"], card["contextTokens"])
        qwen7 = catalog["qwen2.5-7b-instruct"]
        self.assertEqual((qwen7["contextTokens"], qwen7["maxOutputTokens"]), (32768, 8192))
        mixture = catalog["qwen3-30b-a3b-instruct-2507"]
        self.assertEqual((mixture["parametersB"], mixture["activeParametersB"]), (30.5, 3.3))
        self.assertIsNone(mixture["maxOutputTokens"])
        self.assertEqual(mixture["fit"], "limited")

    def test_catalog_even_when_profile_or_runtime_is_unavailable(self):
        for system, accelerator, ram, details, label in (
                ("linux", "cpu", None, {}, "unknown"), ("macos", "apple", 4, {}, "limited"),
                ("macos", "unknown", 32, {"arch": "x86_64"}, "unsupported"),
                ("windows", "discrete", 32, {"arch": "amd64", "avx2": False}, "unsupported")):
            result = advise(system, accelerator, ram, **details)
            self.assertIsNone(result["primary"])
            self.assertIsNone(result["alternative"])
            self.assertEqual({card["fit"] for card in result["catalog"]}, {label})

    def test_suggestions_cover_both_ends_of_the_memory_range(self):
        self.assertEqual(advise("linux", "cpu", 8)["alternative"]["id"], "qwen2.5-0.5b-instruct")
        self.assertEqual(advise("macos", "apple", 32)["alternative"]["id"], "gemma-3-12b-it")
        self.assertEqual(advise("windows", "discrete", 64, 16)["alternative"]["id"], "qwen2.5-14b-instruct")
        self.assertEqual(advise("macos", "apple", 48)["alternative"]["id"], "qwen3-30b-a3b-instruct-2507")

    def test_manual_validation(self):
        for data in ([], {"platform": {}}, {"accelerator": ["cpu"]}, {"ramGiB": 0}, {"ramGiB": "16"},
                     {"vramGiB": True}, {"ramGiB": float("nan")}, {"ramGiB": float("inf")},
                     {"ramGiB": 10 ** 600}, {"platform": "windows", "accelerator": "apple"}):
            with self.assertRaises(ValueError):
                advisor.recommend(data)

    def test_apple_detection_under_rosetta(self):
        queries = {"hw.memsize": str(24 << 30), "machdep.cpu.brand_string": "", "hw.optional.arm64": "1"}
        with patch.object(advisor.platform, "system", return_value="Darwin"), \
             patch.object(advisor.platform, "machine", return_value="x86_64"), \
             patch.object(advisor, "_command", side_effect=lambda args: queries[args[-1]]):
            p = advisor.detect_hardware()
            self.assertEqual((p["arch"], p["accelerator"], p["ramGiB"], p["vramGiB"]), ("arm64", "apple", 24, None))

    def test_detects_only_largest_individual_nvidia_device(self):
        output = "NVIDIA A, 8192\nNVIDIA B, 10240\nUnavailable, N/A\nNVIDIA C, 6144\n"
        with patch.object(advisor.shutil, "which", return_value="/usr/bin/nvidia-smi"), \
             patch.object(advisor, "_command", return_value=output):
            self.assertEqual(advisor._nvidia(), ("NVIDIA B", 10))
        with patch.object(advisor.shutil, "which", return_value=None):
            self.assertEqual(advisor._nvidia(), ("", None))

    def test_windows_avx2_version_guard_and_recommendation(self):
        feature = Mock(return_value=0)
        api = SimpleNamespace(IsProcessorFeaturePresent=feature)
        with patch.object(advisor.sys, "getwindowsversion", return_value=SimpleNamespace(build=18363), create=True), \
             patch.object(ctypes, "WinDLL", return_value=api, create=True):
            self.assertIsNone(advisor._windows_avx2())
            feature.assert_not_called()
        with patch.object(advisor.sys, "getwindowsversion", return_value=SimpleNamespace(build=19041), create=True), \
             patch.object(ctypes, "WinDLL", return_value=api, create=True):
            self.assertFalse(advisor._windows_avx2())
            feature.assert_called_once_with(40)
            feature.return_value = 1
            self.assertTrue(advisor._windows_avx2())
        self.assertIsNone(advise("windows", "discrete", 64, 12, arch="AMD64", avx2=False)["primary"])
        self.assertIsNotNone(advise("windows", "discrete", 64, 12, arch="AMD64", avx2=None)["primary"])
        self.assertIsNotNone(advise("windows", "discrete", 64, 12, arch="ARM64", avx2=False)["primary"])

    def test_windows_dedicated_memory_child_and_timeout_fallback(self):
        with patch.object(advisor.os, "name", "nt"), patch.object(advisor, "_command") as run:
            run.return_value = json.dumps(["AMD Radeon", 12 << 30])
            self.assertEqual(advisor._windows_gpu(), ("AMD Radeon", 12))
            self.assertEqual(run.call_args.args[0][0:3], [advisor.sys.executable, "-I", "-S"])
            self.assertEqual(run.call_args.args[0][-1], "--windows-gpu")
            for missing in ("", "null", "[]", "not available", '["AMD Radeon",0]'):
                run.return_value = missing
                self.assertEqual(advisor._windows_gpu(), ("", None))

    def test_windows_hardware_unavailable_and_linux_memory(self):
        with patch.object(advisor.platform, "system", return_value="Windows"), \
             patch.object(advisor, "_windows_ram", side_effect=OSError), patch.object(advisor, "_nvidia", return_value=("", None)):
            self.assertTrue(advisor.recommend()["needsHardware"])
        with patch.object(advisor.platform, "system", return_value="Linux"), \
             patch.object(advisor, "_linux_cpu", return_value="CPU"), patch.object(advisor, "_nvidia", return_value=("RTX", 10)), \
             patch.object(advisor.os, "sysconf", create=True, side_effect=lambda key: (32 << 18) if key == "SC_PHYS_PAGES" else 4096):
            hardware = advisor.detect_hardware()
            self.assertEqual((hardware["ramGiB"], hardware["gpuName"], hardware["vramGiB"]), (32, "RTX", 10))


if __name__ == "__main__":
    unittest.main()
