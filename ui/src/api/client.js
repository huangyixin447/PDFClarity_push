// REST 客户端:统一 fetch 封装 + 超时 + 错误归一化。
// M2 修复(M1 遗留 L3):网络失败 / 超时 / HTTP 错误给出可读信息,而非裸 "HTTP 500:"。
const BASE = "/api";
const TIMEOUT = 10000;

async function request(url, { method = null, body = null, isForm = false,
                              timeout = TIMEOUT } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    let res;
    try {
      res = await fetch(BASE + url, {
        // method 显式传入优先;默认仍按 body 推断(兼容现有调用)
        method: method ?? (body ? "POST" : "GET"),
        headers: isForm ? {} : { "Content-Type": "application/json" },
        body,
        signal: ctrl.signal,
      });
    } catch (e) {
      if (e.name === "AbortError") throw new Error("请求超时,请检查后端服务");
      throw new Error("无法连接后端服务,请确认服务已启动");
    }
    if (!res.ok) {
      // 优先解析 FastAPI 的 {"detail": ...}
      let detail = "";
      try { const j = await res.json(); detail = j.detail ?? ""; } catch { /* 非 JSON 响应体 */ }
      if (typeof detail === "string" && detail) {
        throw new Error(`HTTP ${res.status}: ${detail.slice(0, 200)}`);
      }
      const text = await res.text().catch(() => "");
      // dev 模式:后端未启动时 vite 代理返回 500,响应体为空或为代理错误文本(均非 JSON)
      if (res.status >= 500 && (!text || /ECONNREFUSED|socket hang up|proxy error/i.test(text))) {
        throw new Error("无法连接后端服务,请确认服务已启动");
      }
      throw new Error(`HTTP ${res.status}: ${text.slice(0, 200)}`);
    }
    return res.json();
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  getState: () => request("/state"),
  getLogs: (file, offset) => request(`/logs?file=${file}&offset=${offset}`),
  inspect: (path) => request(`/files/inspect?path=${encodeURIComponent(path)}`),
  upload: (file) => {
    const fd = new FormData();
    fd.append("file", file);
    return request("/files/upload", { body: fd, isForm: true, timeout: 60000 });
  },
  createJob: (payload) => request("/jobs", { body: JSON.stringify(payload) }),
  cancelJob: (id) => request(`/jobs/${id}/cancel`, { method: "POST" }),
  // 注:取消是 POST(有副作用),不是 GET
};
