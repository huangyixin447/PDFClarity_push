"""路径常量。所有脚本/服务均从本模块导入,避免硬编码。

注意:本文件位于 core/ 子目录,__file__ 指向 core/,项目根需再向上取一层。
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根目录


def _runtime_base():
    """数据根目录:开发态=项目根;打包态(sys.frozen)= %LOCALAPPDATA%/PDFClarity。

    PyInstaller 打包后 HERE 指向解包临时目录 _MEIPASS,退出即被清理;
    work/ output/ 是运行时数据,必须落在持久目录。bin/ scripts/ ui/ 属随包
    资源,仍取 HERE(见下)。
    """
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        base = os.path.join(base, "PDFClarity")
        os.makedirs(base, exist_ok=True)
        return base
    return HERE


# 随包资源(打包后位于 _MEIPASS):超分引擎 / 模型 / 脚本 / 前端产物
ENGINE_EXE = os.path.join(HERE, "bin", "realesrgan", "realesrgan-ncnn-vulkan.exe")
ENGINE_MAC = os.path.join(HERE, "bin", "realesrgan", "realesrgan-ncnn-vulkan")
MODEL_DIR = os.path.join(HERE, "bin", "realesrgan", "models")
EXTRACT = os.path.join(HERE, "scripts", "extract_pages.py")
REBUILD = os.path.join(HERE, "scripts", "rebuild_pdf.py")

# 运行时数据(开发态=项目根;打包态=用户目录,持久):中间文件 / 成品
WORK_ROOT = os.path.join(_runtime_base(), "work")
OUT_ROOT = os.path.join(_runtime_base(), "output")
