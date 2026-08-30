"""M0 冒烟入口:仅启动本地 HTTP 服务,供 curl / PowerShell 验证。

pywebview 桌面壳在 M2 里程碑接入;本文件后续会改为「启动 uvicorn + 打开窗口」。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_VENDOR = os.path.join(_HERE, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

import uvicorn

from server.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
