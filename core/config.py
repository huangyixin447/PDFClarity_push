"""用户配置(JSON 持久化)。目前只有「输出位置」。

- out_mode = "fixed"  : 所有成品写入 out_dir(默认 paths.OUT_ROOT);
- out_mode = "source" : 每个成品写到其源 PDF 所在目录 —— 递归批量时不同子目录
                        下的同名 PDF 因此不再互相覆盖。

配置文件落在运行时数据根目录(开发态=项目根;打包态=用户目录),读写加锁。
读取时任何异常(缺失/损坏/无权限)都静默回落默认值,不阻断主流程;
写入失败则抛 ValueError,由调用方反馈给用户。
"""
import json
import os
import threading

from core.paths import CONFIG_PATH, OUT_ROOT

MODES = ("fixed", "source")

_lock = threading.RLock()
_cache = None


def _defaults():
    return {"out_mode": "fixed", "out_dir": OUT_ROOT}


def load():
    """返回配置副本(首次调用读盘,之后走内存缓存)。"""
    global _cache
    with _lock:
        if _cache is None:
            cfg = _defaults()
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if data.get("out_mode") in MODES:
                        cfg["out_mode"] = data["out_mode"]
                    d = data.get("out_dir")
                    if isinstance(d, str) and d.strip():
                        cfg["out_dir"] = d
            except (OSError, ValueError):
                pass                      # 无配置 / 损坏 → 默认值
            _cache = cfg
        return dict(_cache)


def validate_dir(d):
    """校验并规范化输出目录:展开 ~、转绝对路径、必要时创建、检查可写。"""
    if not isinstance(d, str) or not d.strip():
        raise ValueError("输出目录不能为空")
    ap = os.path.abspath(os.path.expanduser(d.strip()))
    if os.path.exists(ap) and not os.path.isdir(ap):
        raise ValueError("该路径已存在且不是文件夹")
    try:
        os.makedirs(ap, exist_ok=True)
    except OSError as e:
        raise ValueError(f"无法创建输出目录:{e}")
    if not os.access(ap, os.W_OK):
        raise ValueError("输出目录不可写")
    return ap


def save(out_mode=None, out_dir=None):
    """更新并落盘,返回新配置。校验/写入失败抛 ValueError(此时不改内存缓存)。"""
    global _cache
    with _lock:
        cfg = load()
        if out_mode is not None:
            if out_mode not in MODES:
                raise ValueError(f"out_mode 只能是 {' / '.join(MODES)}")
            cfg["out_mode"] = out_mode
        if out_dir is not None:
            cfg["out_dir"] = validate_dir(out_dir)
        try:
            parent = os.path.dirname(CONFIG_PATH)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError as e:
            raise ValueError(f"配置无法保存:{e}")
        _cache = cfg
        return dict(cfg)


def out_dir_for(pdf):
    """某个 PDF 的成品目录。source 模式 = 该 PDF 所在目录。"""
    cfg = load()
    if cfg["out_mode"] == "source":
        return os.path.dirname(os.path.abspath(pdf))
    return cfg["out_dir"]


def fixed_out_dir():
    """固定模式配置的输出目录(source 模式下仍返回它,供界面展示/兜底清理)。"""
    return load()["out_dir"]


def is_source_mode():
    return load()["out_mode"] == "source"
