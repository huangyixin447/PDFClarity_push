import { defineStore } from "pinia";
import { api } from "../api/client";
import { createPoller } from "../api/poller";

// 三文件全量轮询:任意 tab 实时 + 可追溯(不丢行、任务间不串)
const LOG_FILES = ["extract", "upscale", "rebuild"];
const MAX_LOG_LINES = 50000;   // [M3] 异常兜底:正常任务日志 <2k 行;超限才截断

export const useAppState = defineStore("appState", {
  state: () => ({
    state: null,                       // GET /api/state 最近一次快照
    logs: { extract: [], upscale: [], rebuild: [] },
    offsets: { extract: 0, upscale: 0, rebuild: 0 },
    logsTruncated: { extract: false, upscale: false, rebuild: false },
    pollError: null,                   // 最近一次轮询错误(用于界面提示)
    lastPoll: null,                    // 最近一次成功时间戳
    poller: null,                      // 轮询器实例(start() 中创建)
  }),

  getters: {
    running: (s) => s.state?.running ?? false,
    phase: (s) => s.state?.phase ?? "idle",
    percent: (s) => s.state?.percent ?? 0,
  },

  actions: {
    // 一次完整轮询:state + 三文件日志增量,串行发起(保持简单;需要并行可改 Promise.all)
    async pollOnce() {
      // 空闲暂停:页面隐藏且无任务时,跳过本次(不发网络请求)
      if (document.hidden && !this.running) return;

      const st = await api.getState();
      // job_id 变化 => 新任务,日志游标全部归零(旧日志清空)
      if (this.state && this.state.job_id !== st.job_id) this.resetLogs();
      this.state = st;

      // 空闲且无历史日志时不拉日志;done/error/canceled 后仍要拉齐最后几行
      const needLogs = st.running
        || this.logs.extract.length || this.logs.upscale.length || this.logs.rebuild.length;
      if (needLogs) {
        for (const f of LOG_FILES) {
          const res = await api.getLogs(f, this.offsets[f]);
          if (res.next_offset < this.offsets[f]) {   // 文件被重建/截断 → 游标回退 → 历史作废
            this.logs[f] = [];
            this.logsTruncated[f] = false;
          }
          this.offsets[f] = res.next_offset;
          if (res.lines.length) {
            this.logs[f].push(...res.lines);
            if (this.logs[f].length > MAX_LOG_LINES) {
              this.logs[f].splice(0, this.logs[f].length - MAX_LOG_LINES);
              this.logsTruncated[f] = true;
            }
          }
        }
      }
      this.lastPoll = Date.now();
      this.pollError = null;
    },

    resetLogs() {
      this.logs = { extract: [], upscale: [], rebuild: [] };
      this.offsets = { extract: 0, upscale: 0, rebuild: 0 };
      this.logsTruncated = { extract: false, upscale: false, rebuild: false };
    },

    start() {
      if (!this.poller) {
        this.poller = createPoller(() => this.pollOnce());
        // 轮询失败时把错误透出给界面(退避期间仍可提示用户)
        this.poller.setOnError((e) => { this.pollError = e.message; });
      }
      this.poller.start();
    },
    stop() {
      this.poller?.stop();
    },
  },
});
