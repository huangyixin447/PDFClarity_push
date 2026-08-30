"""路径常量。所有脚本/服务均从本模块导入,避免硬编码。

注意:本文件位于 core/ 子目录,__file__ 指向 core/,项目根需再向上取一层。
"""
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根目录
ENGINE_EXE = os.path.join(HERE, "bin", "realesrgan", "realesrgan-ncnn-vulkan.exe")
ENGINE_MAC = os.path.join(HERE, "bin", "realesrgan", "realesrgan-ncnn-vulkan")
MODEL_DIR = os.path.join(HERE, "bin", "realesrgan", "models")
EXTRACT = os.path.join(HERE, "scripts", "extract_pages.py")
REBUILD = os.path.join(HERE, "scripts", "rebuild_pdf.py")
WORK_ROOT = os.path.join(HERE, "work")
OUT_ROOT = os.path.join(HERE, "output")
