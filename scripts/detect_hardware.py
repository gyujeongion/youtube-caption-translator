"""Detect this machine's hardware and recommend a local speech-to-text setup.

    python3 detect_hardware.py            # human-readable summary
    python3 detect_hardware.py --json     # machine-readable (for agents)

Works on macOS, Windows and Linux with the Python standard library only.
The recommendation comes from the tier table in references/stt-local.md
(researched 2026-09-22). Model names, sizes and speeds change quickly:
`table_stale` turns true after 120 days, and the agent must re-verify the
"best model today" (recipe in references/stt-local.md) before recommending.

Accuracy for Korean on small models is UNVERIFIED, so low-RAM tiers carry a
warning. Nothing is installed by this script; it only prints the command.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as _dt
import json
import os
import platform
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

TABLE_DATE = "2026-09-22"
TABLE_STALE_DAYS = 120

# model family -> per-runtime model id, download size (MB) where sourced
MODEL_FAMILIES = {
    "large-v3-turbo": {
        "whisper.cpp": ("ggml-large-v3-turbo.bin", 1620),
        "mlx-whisper": ("mlx-community/whisper-large-v3-turbo", 1610),
        "faster-whisper": ("turbo", 1620),
    },
    "large-v3-turbo-q5_0": {
        "whisper.cpp": ("ggml-large-v3-turbo-q5_0.bin", 574),
        "mlx-whisper": ("mlx-community/whisper-large-v3-turbo", 1610),
        "faster-whisper": ("turbo", 1620),
    },
    "large-v3": {
        "whisper.cpp": ("ggml-large-v3.bin", 3100),
        "faster-whisper": ("large-v3", 3000),  # size UNVERIFIED (~3 GB fp16 expected)
    },
    "small": {
        "whisper.cpp": ("ggml-small-q5_1.bin", 190),
        "faster-whisper": ("small", 480),  # size approximate
    },
}

MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{file}"


def _run(cmd: list[str], timeout: int = 8) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _powershell_exe() -> str | None:
    """Windows PowerShell 5.1 (always present on Windows 10/11), else PowerShell 7 (pwsh)."""
    for name in ("powershell", "pwsh"):
        found = shutil.which(name)
        if found:
            return found
    root = os.environ.get("SystemRoot") or os.environ.get("windir")
    if root:
        cand = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if os.path.isfile(cand):
            return cand
    return None


def _powershell(script: str, timeout: int = 25) -> str:
    """Run a PowerShell snippet and return its stdout decoded as UTF-8.

    -NoProfile skips the user's profile (slow, may print banners); output encoding is forced to UTF-8
    (piped PowerShell otherwise emits the OEM codepage); no double quotes are used inside snippets so
    Windows argument quoting cannot mangle them. First start can take several seconds, hence 25 s.
    wmic is deliberately NOT used: it is removed from current Windows 11.
    """
    exe = _powershell_exe()
    if not exe:
        return ""
    prefix = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
    return _run([exe, "-NoProfile", "-NonInteractive", "-Command", prefix + script],
                timeout=timeout).lstrip("\ufeff")  # a UTF-8 BOM, if the host emits one, must not stick to the first name


def _winreg_value(subkey: str, name: str) -> str:
    """Read one string from HKEY_LOCAL_MACHINE (Windows only; '' anywhere else or on any error)."""
    try:
        import winreg  # type: ignore[import-not-found]
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as k:
            return str(winreg.QueryValueEx(k, name)[0]).strip()
    except (ImportError, OSError, ValueError):
        return ""


def _os_name() -> str:
    s = platform.system().lower()
    return {"darwin": "macos"}.get(s, s or "unknown")


def _cpu_name(os_name: str) -> str:
    if os_name == "macos":
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor()
    if os_name == "linux":
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    if os_name == "windows":
        # registry first: instant and needs no subprocess; PowerShell CIM as the second opinion
        name = _winreg_value(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")
        if not name:
            name = _powershell("(Get-CimInstance Win32_Processor | Select-Object -First 1).Name")
        if name:
            return re.sub(r"\s+", " ", name.splitlines()[0]).strip()
    return platform.processor() or platform.machine()


class MemoryStatusEx(ctypes.Structure):
    """Win32 MEMORYSTATUSEX (64 bytes). Fixed-width fields so the layout is right on any host."""
    _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64)]


def _windows_ram_gb() -> float:
    """Installed RAM visible to Windows via GlobalMemoryStatusEx (0.0 when the call fails)."""
    try:
        st = MemoryStatusEx()
        st.dwLength = ctypes.sizeof(MemoryStatusEx)
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))  # type: ignore[attr-defined]
        if ok and st.ullTotalPhys:
            return round(st.ullTotalPhys / 1024**3, 1)
    except (OSError, AttributeError, ValueError):
        pass
    out = _powershell("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory")
    try:
        return round(int(out.split()[0]) / 1024**3, 1)
    except (ValueError, IndexError):
        return 0.0


def _ram_gb(os_name: str) -> float:
    try:
        if os_name == "macos":
            return round(int(_run(["sysctl", "-n", "hw.memsize"])) / 1024**3, 1)
        if os_name == "linux":
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return round(int(line.split()[1]) / 1024**2, 1)
        if os_name == "windows":
            return _windows_ram_gb()
    except (ValueError, OSError, AttributeError):
        pass
    return 0.0


def _nvidia_smi() -> str | None:
    """nvidia-smi on PATH, else the classic Windows install folder (older drivers keep it off PATH)."""
    found = shutil.which("nvidia-smi")
    if found or not C.is_windows():
        return found
    for var in ("ProgramFiles", "SystemRoot"):
        base = os.environ.get(var)
        if base:
            for rel in (("NVIDIA Corporation", "NVSMI", "nvidia-smi.exe"), ("System32", "nvidia-smi.exe")):
                cand = os.path.join(base, *rel)
                if os.path.isfile(cand):
                    return cand
    return None


def _windows_registry_gpus() -> str:
    """Display adapter names from the registry (used only when PowerShell is unavailable/blocked)."""
    names: list[str] = []
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    for i in range(8):
        n = _winreg_value(f"{base}\\{i:04d}", "DriverDesc")
        if n and n not in names:
            names.append(n)
    return "\n".join(names)


def _gpus(os_name: str, apple_silicon: bool, cpu: str, ram_gb: float) -> list[dict]:
    gpus: list[dict] = []
    if apple_silicon:
        gpus.append({"vendor": "apple", "name": cpu or "Apple GPU", "vram_gb": None})  # unified memory
        return gpus
    smi = _nvidia_smi()
    if smi:
        out = _run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        for line in out.splitlines():
            name, _, mem = line.rpartition(",")
            try:
                gpus.append({"vendor": "nvidia", "name": name.strip(),
                             "vram_gb": round(float(mem) / 1024, 1)})
            except ValueError:
                continue
    other = ""
    if os_name == "linux" and shutil.which("lspci"):
        other = _run(["lspci"])
        other = "\n".join(l for l in other.splitlines() if re.search(r"VGA|3D|Display", l))
    elif os_name == "windows":
        other = _powershell("Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }")
        if not other:
            other = _windows_registry_gpus()
    elif os_name == "macos":
        other = "\n".join(l for l in _run(["system_profiler", "SPDisplaysDataType"], timeout=15).splitlines()
                          if "Chipset Model" in l)
    for line in other.splitlines():
        low = line.lower()
        if "nvidia" in low and any(g["vendor"] == "nvidia" for g in gpus):
            continue
        for vendor, pat in (("nvidia", "nvidia"), ("amd", r"amd|radeon|advanced micro"), ("intel", "intel")):
            if re.search(pat, low):
                if not any(g["name"] and g["name"] in line for g in gpus):
                    gpus.append({"vendor": vendor, "name": line.strip(), "vram_gb": None,
                                 "discrete": is_discrete_gpu(vendor, line)})
                break
    return gpus


def is_discrete_gpu(vendor: str, name: str) -> bool:
    """Heuristic: is this AMD/Intel adapter a discrete card (not an integrated GPU)?

    Integrated examples: 'AMD Radeon(TM) Graphics', 'Radeon 780M', 'Intel(R) UHD Graphics',
    'Iris Xe', 'Intel(R) Arc(TM) Graphics' (Core Ultra iGPU). Discrete examples: 'Radeon RX 7800 XT',
    'Radeon Pro W7800', 'Intel Arc A770 / B580'.
    """
    if vendor == "amd":
        return bool(re.search(r"\bRX\s?\d{3,4}|Radeon\s+Pro|Radeon\s+VII|Instinct|\bR9\b", name, re.I))
    if vendor == "intel":
        # Arc A/B-series incl. laptop 'A370M/A730M' and 'Pro A40/A60'; NOT 'Arc(TM) Graphics' / 'Arc 140V' iGPUs
        return bool(re.search(r"\bArc\b[^\n]*?\b(?:Pro\s+)?[AB]\d{2,3}M?\b|\b[AB]\d{3}M?\b[^\n]*?\bArc\b",
                              name, re.I))
    return vendor == "nvidia"


def _pip(pkg: str) -> str:
    return f"{C.python_cmd()} -m pip install {pkg}"


def _install_steps(runtime: str, os_name: str, cuda: bool = False, vulkan: bool = False) -> list[str]:
    """Separate, copy-pasteable steps (no && chains: they fail in Windows PowerShell 5.1)."""
    if runtime == "whisper.cpp":
        if os_name == "macos":
            return ["brew install whisper.cpp"]
        flags = " -DGGML_CUDA=1" if cuda else (" -DGGML_VULKAN=1" if vulkan else "")
        exe_dir = "whisper.cpp\\build\\bin\\Release" if os_name == "windows" else "whisper.cpp/build/bin"
        return [
            "git clone https://github.com/ggml-org/whisper.cpp",
            f"cmake -S whisper.cpp -B whisper.cpp/build{flags}",
            "cmake --build whisper.cpp/build -j --config Release",
            f"add the full path of {exe_dir} (the folder holding whisper-cli) to PATH, "
            "or set YTCAPTION_WHISPER_CLI to the full path of whisper-cli",
        ]
    if runtime == "mlx-whisper":
        return [_pip("mlx-whisper")] if os_name == "macos" else []
    if runtime == "faster-whisper":
        return [_pip("faster-whisper")]
    return []


def _install_cmd(runtime: str, os_name: str, cuda: bool = False, vulkan: bool = False) -> str:
    return "\n".join(_install_steps(runtime, os_name, cuda, vulkan))


PIP_VENV_NOTE = (
    "If pip answers 'externally-managed-environment' (Homebrew/Debian Python), create a virtual environment first "
    "(python3 -m venv ytcaption-venv, then run its python -m pip install ... and run these scripts with that python).")


def _ram_bucket(ram: float) -> int:
    """Tolerant RAM bucket: OSes report slightly under the marketing size (7.8 GB for 8 GB, ...)."""
    if ram >= 31:
        return 32
    if ram >= 11.5:   # 16 GB laptops with 1-2 GB reserved for the iGPU report 13.9-15.4 GB; 12 GB is fine too
        return 16
    if ram >= 6.5:    # 8 GB machines with an iGPU reservation report ~6.8-7.7 GB
        return 8
    return 4 if ram <= 4.5 else 6


def recommend(info: dict) -> tuple[str, dict]:
    """Pick a tier from the research table -> (tier, recommended). Pure function of `info`."""
    os_name = info["os"]
    ram = float(info.get("ram_gb") or 0)
    bucket = _ram_bucket(ram)
    gpus = info.get("gpus", [])
    apple = bool(info.get("apple_silicon"))
    nvidia = [g for g in gpus if g["vendor"] == "nvidia"]
    discrete = [g for g in gpus if g["vendor"] in ("amd", "intel") and g.get("discrete")]
    best_vram = max((g.get("vram_gb") or 0 for g in nvidia), default=0)
    warnings: list[str] = []
    compute_type = "int8"
    extra: dict = {}
    prefer_cloud, reason = False, "This machine can run a local model at reasonable quality."

    if apple and bucket >= 32:
        tier, runtime, family = "apple-silicon-32gb+", "whisper.cpp", "large-v3-turbo"
        extra["alternative"] = ("ggml-large-v3-q5_0.bin (1.08 GB) or ggml-large-v3.bin (3.1 GB) for maximum quality; "
                                "whether large-v3 beats turbo on Korean is UNVERIFIED")
    elif apple and bucket >= 16:
        tier, runtime, family = "apple-silicon-16gb", "whisper.cpp", "large-v3-turbo"
    elif apple:
        tier, runtime, family = "apple-silicon-8gb", "whisper.cpp", "large-v3-turbo-q5_0"
        extra["fallback"] = "ggml-small-q5_1.bin (190 MB) if the machine struggles"
        warnings.append("8 GB Apple Silicon: RAM use of the turbo q5_0 model is UNVERIFIED; "
                        "if it swaps, fall back to the small model (lower Korean accuracy).")
    elif nvidia and best_vram >= 7.5:
        tier, runtime, family = "nvidia-8gb+", "faster-whisper", "large-v3"
        compute_type = "float16"
    elif nvidia and best_vram >= 4:
        tier, runtime, family = "nvidia-4-6gb", "faster-whisper", "large-v3-turbo"
        compute_type = "int8_float16"
    elif bucket >= 16:
        tier, runtime, family = "cpu-16gb+", "faster-whisper", "large-v3-turbo"
        warnings.append("CPU-only: turbo speed on CPU is UNVERIFIED (expect it to be slower than real time on old machines).")
    elif bucket == 8:
        tier, runtime, family = "cpu-8gb", "faster-whisper", "small"
        prefer_cloud = True
        reason = ("8 GB CPU-only machine: only the small model is practical and Korean accuracy on it is "
                  "UNVERIFIED and likely poor -- an editor SRT or a cloud STT engine is the better path.")
    else:
        tier, runtime, family = "low-ram", "faster-whisper", "small"
        prefer_cloud = True
        reason = ("Under 8 GB RAM: local Korean transcription is unlikely to be good enough -- use an editor SRT "
                  "or a cloud STT engine.")
        if ram <= 4.5:
            warnings.append("Low RAM (<= 4 GB): only tiny/base/small models fit, and accuracy for Korean on "
                            "those is UNVERIFIED and likely poor. Prefer a cloud STT engine or an SRT from your editor.")
        else:
            warnings.append("Under 8 GB RAM: stick to the small model; Korean accuracy on it is UNVERIFIED.")

    if family == "small":
        warnings.append("Korean accuracy on Whisper small is UNVERIFIED and likely lower than turbo; "
                        "spot-check the first minute before trusting the result.")
    if nvidia and os_name == "windows":
        warnings.append("Windows + NVIDIA + faster-whisper needs the CUDA 12 toolkit (cuBLAS) on PATH; "
                        "'cublas64_12.dll not found' means it is missing. If it keeps failing, the run falls back "
                        "to CPU int8 automatically (see references/stt-local.md, Windows gotchas).")
    if not apple and not nvidia and os_name == "windows":
        warnings.append("faster-whisper on CPU works without CUDA; ignore any cuBLAS DLL advice.")
    if runtime in ("faster-whisper",) and os_name != "windows":
        warnings.append(PIP_VENV_NOTE)
    if discrete and not apple and not nvidia and os_name in ("windows", "linux"):
        names = ", ".join(g["name"] for g in discrete)
        extra["advanced_option"] = {
            "runtime": "whisper.cpp",
            "note": f"Discrete AMD/Intel GPU detected ({names}): an experimental Vulkan build of whisper.cpp "
                    "can use it. Advanced users only; faster-whisper has no AMD/Intel GPU support.",
            "install_steps": _install_steps("whisper.cpp", os_name, vulkan=True),
        }

    model_id, mb = MODEL_FAMILIES[family].get(runtime) or MODEL_FAMILIES[family]["whisper.cpp"]
    steps = _install_steps(runtime, os_name, cuda=False)
    rec = {
        "runtime": runtime,
        "model": family,
        "model_id": model_id,
        "approx_download_mb": mb,
        "install_cmd": "\n".join(steps),
        "install_steps": steps,
        "compute_type": compute_type,
        "prefer_cloud": prefer_cloud,
        "prefer_cloud_reason": reason,
        "warnings": warnings,
    }
    if runtime == "whisper.cpp":
        rec["model_download"] = MODEL_URL.format(file=model_id)
    rec.update(extra)
    return tier, rec


def model_for(runtime: str, family: str) -> tuple[str, int]:
    """Model id for (runtime, family), degrading to a family the runtime has."""
    fam = MODEL_FAMILIES.get(family) or MODEL_FAMILIES["large-v3-turbo"]
    if runtime in fam:
        return fam[runtime]
    for alt in ("large-v3-turbo", "small"):
        if runtime in MODEL_FAMILIES[alt]:
            return MODEL_FAMILIES[alt][runtime]
    return fam["whisper.cpp"]


def is_stale(table_date: str = TABLE_DATE, today: _dt.date | None = None) -> bool:
    today = today or _dt.date.today()
    return (today - _dt.date.fromisoformat(table_date)).days > TABLE_STALE_DAYS


def detect(today: _dt.date | None = None) -> dict:
    os_name = _os_name()
    arch = platform.machine().lower()
    apple = os_name == "macos" and arch in ("arm64", "aarch64")
    cpu = _cpu_name(os_name)
    ram = _ram_gb(os_name)
    info = {
        "os": os_name,
        "arch": arch,
        "cpu": cpu,
        "ram_gb": ram,
        "gpus": _gpus(os_name, apple, cpu, ram),
        "apple_silicon": apple,
    }
    tier, rec = recommend(info)
    info.update({
        "tier": tier,
        "recommended": rec,
        "table_date": TABLE_DATE,
        "table_stale": is_stale(TABLE_DATE, today),
    })
    return info


def format_report(info: dict) -> str:
    rec = info["recommended"]
    lines = [
        f"OS:        {info['os']} ({info['arch']})",
        f"CPU:       {info['cpu']}",
        f"RAM:       {info['ram_gb']} GB",
    ]
    if info["gpus"]:
        for g in info["gpus"]:
            v = f", {g['vram_gb']} GB VRAM" if g.get("vram_gb") else (", unified memory" if g["vendor"] == "apple" else "")
            lines.append(f"GPU:       {g['vendor']} {g['name']}{v}")
    else:
        lines.append("GPU:       none detected")
    lines += [
        "",
        f"Tier:      {info['tier']}",
        f"Runtime:   {rec['runtime']}",
        f"Model:     {rec['model']}  ({rec['model_id']}, ~{rec['approx_download_mb']} MB download)",
    ]
    steps = rec.get("install_steps") or []
    if steps:
        lines.append("Install (run each step separately):")
        lines.extend(f"  {i}. {st}" for i, st in enumerate(steps, 1))
    else:
        lines.append("Install:   (see references/stt-local.md)")
    if rec.get("model_download"):
        lines.append(f"Model URL: {rec['model_download']}")
    for k in ("alternative", "fallback"):
        if rec.get(k):
            lines.append(f"{k.capitalize()}: {rec[k]}")
    for w in rec["warnings"]:
        lines.append(f"WARNING:   {w}")
    if rec.get("prefer_cloud"):
        lines.append(f"PREFER CLOUD: {rec['prefer_cloud_reason']}")
    adv = rec.get("advanced_option")
    if adv:
        lines.append(f"Advanced:  {adv['note']}")
    lines.append("")
    lines.append(f"Tier table date: {info['table_date']}"
                 + ("  (STALE > 120 days: re-verify the best model before recommending)" if info["table_stale"] else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    C.setup_utf8_io()
    ap = argparse.ArgumentParser(description="Detect hardware and recommend a local STT setup.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    info = detect()
    if a.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(format_report(info))
    return 0


if __name__ == "__main__":
    sys.exit(main())
