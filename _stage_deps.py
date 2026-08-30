"""C11 打包辅助:把最小运行依赖复制到 build/_stage(仅骨架;完整打包见 M5)。

用法:
    python _stage_deps.py

说明:
- 本机沙箱环境 PyInstaller 的 modulegraph 会把 Anaconda 全量 site-packages
  (panel→datashader 连锁隐藏导入)拉进分析导致超时/失败,因此只复制骨架所需
  包 + 各自的 dist-info 元数据到 build/_stage,配合 `python -S` 隔离分析。
- 跳过 __pycache__;pydantic / starlette 启动时要查版本元数据,必须附带 dist-info。
"""
import os
import shutil
import sysconfig

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.join(HERE, "build", "_stage")
VENDOR = os.path.join(HERE, "vendor")

# 顶层包(含各自子包);fastapi 本身在 vendor/,由 PYTHONPATH 提供,无需复制
PACKAGES = [
    "uvicorn", "click", "colorama", "h11",
    "starlette", "anyio", "idna", "typing_extensions",
    "typing_inspection", "annotated_doc",
    "pydantic", "pydantic_core", "annotated_types",
    "multipart", "python_multipart",
    "psutil", "fitz", "pymupdf", "PIL",
]

# M5 桌面壳依赖(pywebview + pythonnet),装在 vendor/ 而非系统 site-packages
VENDOR_ITEMS = [
    "webview",            # pywebview 主包(含 js/lib/platforms)
    "bottle", "bottle.py",        # webview.http 的后端(文件或目录皆可)
    "proxy_tools",        # webview.menu 依赖
    "clr_loader",         # pythonnet 的 .NET 运行时加载器(含 ClrLoader.dll)
    "cffi",               # clr_loader 的 cffi 后端
    "pycparser",          # cffi 的 C 解析器
    "pythonnet",          # 含 runtime/Python.Runtime.dll 等原生程序集
    "clr.py",             # pythonnet 顶层引导模块
    "typing_extensions", "typing_extensions.py",
]

# 这些包的 dist-info 需随包(版本元数据 + pyinstaller40 hook 入口)
VENDOR_META = [
    "webview", "bottle", "proxy_tools", "clr_loader", "cffi",
    "pycparser", "pythonnet", "typing_extensions",
]


def _copy(src, dst, label):
    """复制文件或目录到 STAGE(跳过 __pycache__)。"""
    if os.path.isdir(src):
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    print(f"  [{label}] {os.path.basename(src)}")


def main():
    sp = sysconfig.get_path("purelib")
    if not sp or not os.path.isdir(sp):
        print(f"!! 找不到 site-packages: {sp}")
        return 1
    print(f"来源(site-packages): {sp}")
    if os.path.isdir(STAGE):
        shutil.rmtree(STAGE)
    os.makedirs(STAGE, exist_ok=True)

    for name in PACKAGES:
        src = os.path.join(sp, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(STAGE, name),
                            ignore=shutil.ignore_patterns("__pycache__"))
            print(f"  [pkg ] {name}")

    # 附带 dist-info(版本元数据)
    for f in os.listdir(sp):
        if f.endswith(".dist-info") and any(f.startswith(p + "-") for p in PACKAGES):
            shutil.copytree(os.path.join(sp, f), os.path.join(STAGE, f),
                            ignore=shutil.ignore_patterns("__pycache__"))
            print(f"  [meta] {f}")

    # M5 桌面壳依赖:从 vendor/ 复制(pywebview/pythonnet 不在 site-packages)
    print(f"来源(vendor): {VENDOR}")
    for name in VENDOR_ITEMS:
        src = os.path.join(VENDOR, name)
        if os.path.exists(src):
            _copy(src, os.path.join(STAGE, name), "vpkg")
        else:
            print(f"  [SKIP] vendor 无 {name}")
    for meta in VENDOR_META:
        for f in os.listdir(VENDOR):
            if f.endswith(".dist-info") and f.startswith(meta + "-"):
                dst = os.path.join(STAGE, f)
                if os.path.exists(dst):
                    print(f"  [SKIP] {f} 已存在(site-packages 已复制)")
                    continue
                _copy(os.path.join(VENDOR, f), dst, "vmeta")

    print(f"完成: {STAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
