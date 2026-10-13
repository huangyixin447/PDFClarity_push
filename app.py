"""PDFClarity 启动入口:默认桌面模式(pywebview 壳),可选纯服务器模式。

M5 起 `python app.py` 打开原生桌面窗口(WebView2):
- 同进程先起 uvicorn(后台线程),再创建 pywebview 窗口加载 http://127.0.0.1:<port>;
- 8000 被占自动顺延;前端走相对路径 /api,无需注入端口;
- 运行中关窗:先请求取消任务并等子进程退出(≤15s)再放行,避免孤儿进程;
- pywebview 不可用时回退为「起服务 + webbrowser 打开默认浏览器」。

用法:
    python app.py                 # 桌面窗口(默认)
    python app.py --server-only   # 仅起服务并打开浏览器(回归 M0~M4 行为)
"""
import argparse
import os
import socket
import sys
import threading
import time
import webbrowser

_HERE = os.path.dirname(os.path.abspath(__file__))
_VENDOR = os.path.join(_HERE, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

from uvicorn.config import Config
from uvicorn.server import Server

from server.main import app


class _ServerThread(Server):
    """在非主线程运行的 uvicorn server。

    必须在子线程运行,否则 uvicorn 自装 SIGINT/SIGTERM 处理器会与主线程
    (pywebview / 驻留循环)冲突;覆写 install_signal_handlers 为空即屏蔽。
    """

    def install_signal_handlers(self):
        pass


def _port_free(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            return True
    except OSError:
        return False


def _find_free_port(start=8000, tries=20):
    """从 start 起顺延 tries 个端口;全被占则退回系统随机端口。"""
    for port in range(start, start + tries):
        if _port_free(port):
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_server(port):
    cfg = Config(app, host="127.0.0.1", port=port, log_level="info")
    server = _ServerThread(cfg)
    threading.Thread(target=server.run, daemon=True, name="uvicorn").start()
    return server


def _wait_until_up(port, timeout=60):
    """轮询 GET /api/state,确认 lifespan(自检)完成后才返回 True。

    超时须大于启动自检耗时:jobs.init() 会同步跑一次引擎 GPU 探测
    (probe_gpus,上限 120s;实测沙箱下约 25s,正常环境数秒)。
    """
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/state", timeout=0.5
            ) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def _hang_forever():
    """主线程驻留(server-only / 浏览器回退模式),等待 Ctrl+C。"""
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n已退出")


def _open_browser(port):
    webbrowser.open(f"http://127.0.0.1:{port}")
    print(f"PDFClarity 服务已启动: http://127.0.0.1:{port}  (Ctrl+C 退出)")


def _start_desktop(port):
    try:
        import webview  # noqa: F401   pywebview 可能因缺 pythonnet/WebView2 导入失败
    except Exception as e:
        print(f"[警告] pywebview 不可用({e}),回退为浏览器模式")
        _open_browser(port)
        _hang_forever()
        return

    from server.bridge import (
        DesktopBridge,
        setup_closing_guard,
        setup_drop_handler,
    )

    bridge = DesktopBridge()
    window = webview.create_window(
        "PDFClarity",
        f"http://127.0.0.1:{port}",
        js_api=bridge,
        width=1280,
        height=800,
        min_size=(960, 640),
        background_color="#FFFFFF",
    )
    bridge.bind_window(window)        # js_api 方法里需要 window 调文件对话框
    setup_closing_guard(window)       # 运行中关窗:先取消任务再放行
    setup_drop_handler(window)        # 页面 loaded 后注册拖拽取路径

    webview.start()                   # 阻塞至窗口关闭;uvicorn 为 daemon 随进程退出


def main():
    parser = argparse.ArgumentParser(description="PDFClarity")
    parser.add_argument(
        "--server-only",
        action="store_true",
        help="仅启动 HTTP 服务并打开浏览器(不开桌面窗口)",
    )
    args = parser.parse_args()

    port = _find_free_port()
    _run_server(port)
    if not _wait_until_up(port):
        print("服务启动失败,请查看上方 uvicorn 日志")
        return 1

    if args.server_only:
        _open_browser(port)
        _hang_forever()
    else:
        _start_desktop(port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
