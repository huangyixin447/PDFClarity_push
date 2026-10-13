"""core 包:CLI 与 GUI 共用的核心逻辑。"""
from core.paths import (HERE, WORK_ROOT, OUT_ROOT, CONFIG_PATH, ENGINE_EXE,
                        ENGINE_MAC, MODEL_DIR, EXTRACT, REBUILD)
from core import config
from core.config import out_dir_for, fixed_out_dir, is_source_mode
from core.gpu import find_engine, probe_gpus, pick_gpu, prepare_engine
from core.progress import count_png, count_tmp_jpg, pdf_page_count
from core.monitor import SysMonitor
from core.statusbar import IS_TTY, StatusBar, enable_ansi
from core.pipeline import process_one, upscale, run_with_progress

__all__ = [
    "HERE", "WORK_ROOT", "OUT_ROOT", "CONFIG_PATH", "ENGINE_EXE", "ENGINE_MAC",
    "MODEL_DIR", "EXTRACT", "REBUILD",
    "config", "out_dir_for", "fixed_out_dir", "is_source_mode",
    "find_engine", "probe_gpus", "pick_gpu", "prepare_engine",
    "count_png", "count_tmp_jpg", "pdf_page_count",
    "SysMonitor", "IS_TTY", "StatusBar", "enable_ansi",
    "process_one", "upscale", "run_with_progress",
]
