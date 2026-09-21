import datetime
import json
import subprocess
import sys
import unittest

import support  # noqa: F401

import _common as C
import detect_hardware as H


def info(os_name="linux", ram=16, gpus=None, apple=False, arch="x86_64"):
    return {"os": os_name, "arch": arch, "cpu": "test", "ram_gb": ram, "gpus": gpus or [], "apple_silicon": apple}


NV = lambda v: [{"vendor": "nvidia", "name": "RTX", "vram_gb": v}]


class TierTests(unittest.TestCase):
    def rec(self, **kw):
        return H.recommend(info(**kw))

    def test_apple_tiers(self):
        t, r = self.rec(os_name="macos", ram=64, apple=True, arch="arm64")
        self.assertEqual((t, r["runtime"], r["model_id"]), ("apple-silicon-32gb+", "whisper.cpp", "ggml-large-v3-turbo.bin"))
        self.assertEqual(r["install_cmd"], "brew install whisper.cpp")
        t, r = self.rec(os_name="macos", ram=16, apple=True, arch="arm64")
        self.assertEqual(t, "apple-silicon-16gb")
        t, r = self.rec(os_name="macos", ram=8, apple=True, arch="arm64")
        self.assertEqual((t, r["model_id"]), ("apple-silicon-8gb", "ggml-large-v3-turbo-q5_0.bin"))
        self.assertTrue(r["warnings"])

    def test_nvidia_tiers(self):
        t, r = self.rec(ram=32, gpus=NV(12))
        self.assertEqual((t, r["runtime"], r["model"], r["compute_type"]), ("nvidia-8gb+", "faster-whisper", "large-v3", "float16"))
        self.assertEqual(r["install_cmd"], f"{C.python_cmd()} -m pip install faster-whisper")
        t, r = self.rec(ram=16, gpus=NV(6))
        self.assertEqual((t, r["model_id"], r["compute_type"]), ("nvidia-4-6gb", "turbo", "int8_float16"))
        self.assertEqual(self.rec(ram=16, gpus=NV(7.99))[0], "nvidia-8gb+")   # 8 GB cards report just under 8

    def test_windows_nvidia_dll_warning(self):
        _, r = self.rec(os_name="windows", ram=32, gpus=NV(12))
        self.assertTrue(any("cublas64_12" in w for w in r["warnings"]))

    def test_cpu_tiers_use_faster_whisper_pip_not_cmake(self):
        t, r = self.rec(ram=32)
        self.assertEqual((t, r["runtime"], r["model_id"], r["compute_type"], r["prefer_cloud"]),
                         ("cpu-16gb+", "faster-whisper", "turbo", "int8", False))
        self.assertNotIn("cmake", r["install_cmd"])
        self.assertIn("-m pip install faster-whisper", r["install_cmd"])
        t, r = self.rec(ram=8)
        self.assertEqual((t, r["runtime"], r["model_id"], r["prefer_cloud"]), ("cpu-8gb", "faster-whisper", "small", True))
        self.assertTrue(r["prefer_cloud_reason"])
        self.assertTrue(any("Korean" in w and "UNVERIFIED" in w for w in r["warnings"]))

    def test_ram_tolerance(self):
        self.assertEqual(self.rec(ram=7.8)[0], "cpu-8gb")
        self.assertEqual(self.rec(ram=15.6)[0], "cpu-16gb+")
        self.assertEqual(self.rec(os_name="macos", ram=31.4, apple=True, arch="arm64")[0], "apple-silicon-32gb+")
        self.assertEqual(self.rec(os_name="macos", ram=15.5, apple=True, arch="arm64")[0], "apple-silicon-16gb")
        self.assertEqual(self.rec(ram=6.0)[0], "low-ram")
        # 8 GB / 16 GB Windows machines that reserve 0.5-2 GB for the integrated GPU report less than the label
        self.assertEqual(self.rec(ram=6.9)[0], "cpu-8gb")
        self.assertEqual(self.rec(ram=13.9)[0], "cpu-16gb+")

    def test_low_ram_warns_about_korean_and_prefers_cloud(self):
        t, r = self.rec(ram=3.8)
        self.assertEqual(t, "low-ram")
        self.assertTrue(r["prefer_cloud"])
        text = " ".join(r["warnings"])
        self.assertIn("Low RAM", text)
        self.assertIn("Korean", text)
        self.assertIn("UNVERIFIED", text)

    def test_apple_never_prefers_cloud(self):
        for ram in (8, 16, 64):
            self.assertFalse(self.rec(os_name="macos", ram=ram, apple=True, arch="arm64")[1]["prefer_cloud"])

    def test_integrated_gpu_does_not_trigger_vulkan(self):
        igpu = [{"vendor": "intel", "name": "Intel(R) UHD Graphics", "vram_gb": None, "discrete": False},
                {"vendor": "amd", "name": "AMD Radeon(TM) Graphics", "vram_gb": None, "discrete": False}]
        t, r = self.rec(os_name="windows", ram=16, gpus=igpu)
        self.assertEqual((t, r["runtime"]), ("cpu-16gb+", "faster-whisper"))
        self.assertNotIn("advanced_option", r)
        self.assertNotIn("amd-intel-gpu", t)

    def test_discrete_gpu_is_advanced_only(self):
        gpu = [{"vendor": "amd", "name": "AMD Radeon RX 7800 XT", "vram_gb": None, "discrete": True}]
        t, r = self.rec(os_name="windows", ram=16, gpus=gpu)
        self.assertEqual(r["runtime"], "faster-whisper")           # main recommendation unchanged
        adv = r["advanced_option"]
        self.assertTrue(any("-DGGML_VULKAN=1" in st for st in adv["install_steps"]))

    def test_is_discrete_gpu(self):
        self.assertTrue(H.is_discrete_gpu("intel", "Intel Arc A770"))
        self.assertTrue(H.is_discrete_gpu("amd", "Radeon RX 6600"))
        for v, n in [("intel", "Intel(R) Arc(TM) Graphics"), ("intel", "Intel(R) Iris(R) Xe Graphics"),
                     ("amd", "AMD Radeon(TM) Graphics"), ("amd", "AMD Radeon 780M")]:
            self.assertFalse(H.is_discrete_gpu(v, n), n)

    def test_install_steps_are_separate_and_powershell_safe(self):
        steps = H._install_steps("whisper.cpp", "windows", vulkan=True)
        self.assertGreaterEqual(len(steps), 4)
        for st in steps:
            self.assertNotIn("&&", st)
        self.assertIn("YTCAPTION_WHISPER_CLI", steps[-1])
        self.assertIn("PATH", steps[-1])
        self.assertIn("-DGGML_VULKAN=1", steps[1])
        self.assertEqual(H._install_cmd("whisper.cpp", "windows"), "\n".join(H._install_steps("whisper.cpp", "windows")))

    def test_recommended_has_steps_and_string_cmd(self):
        _, r = self.rec(ram=32)
        self.assertIsInstance(r["install_cmd"], str)
        self.assertEqual(r["install_cmd"], "\n".join(r["install_steps"]))

    def test_small_model_korean_warning_everywhere(self):
        _, r = self.rec(ram=8)
        self.assertTrue(any("Korean" in w and "UNVERIFIED" in w for w in r["warnings"]))

    def test_cuda_build_flag_for_whispercpp(self):
        self.assertIn("-DGGML_CUDA=1", H._install_cmd("whisper.cpp", "linux", cuda=True))

    def test_model_for_degrades(self):
        self.assertEqual(H.model_for("mlx-whisper", "large-v3")[0], "mlx-community/whisper-large-v3-turbo")
        self.assertEqual(H.model_for("faster-whisper", "small")[0], "small")


class StaleTests(unittest.TestCase):
    def test_stale_after_120_days(self):
        self.assertFalse(H.is_stale("2026-09-22", datetime.date(2026, 12, 20)))   # 89 days
        self.assertFalse(H.is_stale("2026-09-22", datetime.date(2027, 1, 20)))    # 120 days
        self.assertTrue(H.is_stale("2026-09-22", datetime.date(2027, 1, 21)))     # 121 days


class CliTests(unittest.TestCase):
    def test_json_schema(self):
        out = subprocess.run([sys.executable, str(support.SCRIPTS / "detect_hardware.py"), "--json"],
                             capture_output=True, text=True, check=True).stdout
        d = json.loads(out)
        for k in ("os", "arch", "cpu", "ram_gb", "gpus", "apple_silicon", "tier", "recommended", "table_date", "table_stale"):
            self.assertIn(k, d)
        for k in ("runtime", "model", "model_id", "approx_download_mb", "install_cmd", "warnings",
                  "install_steps", "prefer_cloud", "prefer_cloud_reason"):
            self.assertIn(k, d["recommended"])
        self.assertIn(d["recommended"]["runtime"], ("whisper.cpp", "mlx-whisper", "faster-whisper"))
        self.assertEqual(d["table_date"], "2026-09-22")
        self.assertIsInstance(d["table_stale"], bool)

    def test_text_output(self):
        out = subprocess.run([sys.executable, str(support.SCRIPTS / "detect_hardware.py")],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("Tier:", out)


if __name__ == "__main__":
    unittest.main()
