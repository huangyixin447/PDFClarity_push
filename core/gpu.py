"""GPU 探测与选择。"""
import os
import re
import stat
import subprocess
import sys

from core.paths import HERE, ENGINE_EXE, ENGINE_MAC, MODEL_DIR

GPU_RE = re.compile(r"^\s*\[\s*(\d+)\s+(.*?)\]\s+queue", re.MULTILINE)


def find_engine():
    if os.path.isfile(ENGINE_EXE):
        return ENGINE_EXE
    if os.path.isfile(ENGINE_MAC):
        return ENGINE_MAC
    return None


def prepare_engine(engine):
    """macOS 一次性准备:清除引擎的 quarantine 属性并确保可执行位。

    替代原 run.sh 的 `xattr -dr com.apple.quarantine` 与 `[ -x ... ]` 检查,
    使 run.py 成为 macOS 下的完整入口。
    非 macOS 或引擎缺失时静默跳过;全部 best-effort,失败不影响主流程。
    """
    if sys.platform != "darwin" or not engine:
        return
    try:
        subprocess.run(["xattr", "-dr", "com.apple.quarantine",
                        os.path.dirname(engine)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30)
    except Exception:
        pass
    try:
        st = os.stat(engine)
        os.chmod(engine, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def probe_gpus(engine):
    """Run the engine once on a tiny image and read its Vulkan device list.

    The engine prints ``[<id> <name>]  queue...`` for *every* device on any
    real run, so one cheap probe is enough to enumerate them (Windows laptops
    often enumerate the slow iGPU first, e.g. AMD Radeon before an NVIDIA GPU).
    """
    probe_in = os.path.join(HERE, "work", ".gpu_probe_src")
    probe_out = os.path.join(HERE, "work", ".gpu_probe_out")
    os.makedirs(probe_in, exist_ok=True)
    os.makedirs(probe_out, exist_ok=True)
    px = os.path.join(probe_in, "p.png")
    if not os.path.isfile(px):
        try:
            from PIL import Image
            Image.new("RGB", (4, 4), (128, 128, 128)).save(px)
        except Exception:
            return None
    cmd = [engine, "-i", probe_in, "-o", probe_out, "-n", "realesrgan-x4plus",
           "-s", "4", "-m", MODEL_DIR, "-g", "0", "-f", "png"]
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=120)
        text = r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return None
    return [(int(m.group(1)), m.group(2).strip()) for m in GPU_RE.finditer(text)]


def pick_gpu(gpus):
    """Prefer a discrete NVIDIA GPU over integrated graphics."""

    def score(name):
        n = name.lower()
        if "nvidia" in n or "geforce" in n or "rtx" in n or "gtx" in n:
            return 4
        if "radeon" in n and "graphics" not in n:
            return 3
        if "graphics" not in n and "uhd" not in n and "iris" not in n:
            return 2
        return 1

    return max(gpus, key=lambda g: (score(g[1]), -g[0]))[0]
