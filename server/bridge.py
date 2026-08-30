"""M5 桌面壳桥:pywebview js_api + 关闭/拖拽钩子。

桌面模式(webview)与浏览器模式共用同一套 FastAPI 与前端;本模块只承载
「原生文件对话框 / 打开文件夹 / 关闭时取消任务 / 拖拽取绝对路径」等纯桌面
能力,与业务逻辑(server/jobs)解耦。

机制要点(均已对照 vendor/webview 源码核实):
- js_api:create_window 传入的实例方法会暴露到 JS `window.pywebview.api`;
  实例本身拿不到 window,须由 app.py 在创建窗口后 bind_window 注入。
- 关闭拦截:window.events.closing 是同步事件(Event(window, True)),
  任一处理器返回 False 才会阻止关闭;返回 True(或 None)即放行。
- 拖拽取路径:WebView2 后端(4.3+)在 drop 事件里把拖入文件的全路径回填到
  dataTransfer.files[].pywebviewFullPath(见 webview/util.py 的
  pywebviewEventHandler drop 分支);只有注册了 drop 监听器(num_listeners>0)
  才触发原生拖放拦截,故须等页面 loaded 后再注册。
"""
import json
import logging
import os
import subprocess
import sys
import time

import webview

logger = logging.getLogger("pdfclarity.bridge")


class DesktopBridge:
    """通过 js_api 暴露给前端的桌面能力(仅公开方法会序列化到前端)。"""

    def __init__(self):
        self._window = None

    def bind_window(self, window):
        self._window = window

    def select_file(self):
        """弹出原生文件选择框,返回 PDF 绝对路径;取消返回 None。"""
        if self._window is None:
            return None
        try:
            chosen = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                file_types=("PDF 文档 (*.pdf)",),
                allow_multiple=False,
            )
        except Exception as e:
            logger.warning("文件对话框调用失败: %s", e)
            return None
        if not chosen:
            return None
        return chosen[0]

    def open_in_folder(self, path):
        """在文件管理器中定位并选中文件/文件夹。Windows 用 explorer
        /select;macOS 用 `open -R`。"""
        if not path:
            return False
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["explorer", f"/select,{path}"])
            return True
        except Exception as e:
            logger.warning("打开所在目录失败: %s", e)
            return False


def setup_closing_guard(window):
    """运行中关窗:先请求取消任务,轮询等待子进程退出(≤15s)后放行。

    处理器在主线程同步执行,期间窗口冻结是刻意的——等待子进程退出避免孤儿
    realesrgan/extract 进程;15s 上限防止子进程僵死时窗口无法关闭。
    """
    from server import jobs

    def on_closing():
        st = jobs.snapshot()
        if st.get("running"):
            job_id = st.get("job_id")
            logger.info("窗口关闭时任务运行中,请求取消: %s", job_id)
            if job_id:
                jobs.request_cancel(job_id)
            deadline = time.time() + 15
            while time.time() < deadline:
                if not jobs.snapshot().get("running"):
                    break
                time.sleep(0.3)
            if jobs.snapshot().get("running"):
                logger.warning("等待 15s 子进程仍未退出,强制放行关闭")
        return True  # 放行关闭

    window.events.closing += on_closing


def setup_drop_handler(window):
    """页面加载完成后注册 document 级 drop 监听,拖拽文件取绝对路径。

    收到带 pywebviewFullPath 的文件后,经 evaluate_js 通知前端
    (前端须已定义 window.__pdfclarityDesktopPick(path))。
    """

    def on_loaded():
        def on_drop(event):
            files = (event.get("dataTransfer") or {}).get("files", [])
            for f in files:
                path = f.get("pywebviewFullPath")
                if path:
                    logger.info("拖入文件: %s", path)
                    window.evaluate_js(
                        "window.__pdfclarityDesktopPick && "
                        f"window.__pdfclarityDesktopPick({json.dumps(path)})"
                    )
                    break

        try:
            window.dom.document.events.drop += on_drop
            logger.info("桌面拖拽监听已注册")
        except Exception as e:
            logger.warning("注册桌面拖拽监听失败(拖拽将不可用): %s", e)

    window.events.loaded += on_loaded
