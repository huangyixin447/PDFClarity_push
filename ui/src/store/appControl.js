import { defineStore } from "pinia";
import { ElMessage, ElMessageBox } from "element-plus";
// 按需场景下显式引入这两个 JS 组件的样式(不依赖 resolver 处理非 SFC 文件)
import "element-plus/es/components/message/style/css";
import "element-plus/es/components/message-box/style/css";

import { api } from "../api/client";
import { useAppState } from "./appState";

// 模型列表(与 bin/realesrgan/models/ 下的 param 文件对应)
const MODELS = ["realesrgan-x4plus", "realesrgan-x4plus-anime"];
let busy = false;                // 模块级连点保护(selectByPath 防并发 inspect)

export const useAppControl = defineStore("appControl", {
  state: () => ({
    selectedFile: null,            // 当前选中 PDF 的绝对路径(后端可处理)
    fileInfo: null,                // inspect 结果 {name, size, pages, out_path, out_exists}
    view: "select",                // select | work(M3 切到界面二)
    params: {
      scale: 4,
      target_width: 4320,
      model: "realesrgan-x4plus",
      gpu_ids: "",                // "" = 用后端自检的自动选择结果(后端 if not gpu_ids 回落)
      extract_workers: 8,          // 与 CLI 默认 min(8, cpu_count) 对齐;
                                   // 机器核数 < 8 时由 App.vue 首轮轮询后收敛(见 App.vue)
      skip_existing: true,
    },
  }),

  getters: {
    running: () => useAppState().running,
    phase: () => useAppState().phase,
    engineOk: () => useAppState().state?.engine_ok ?? false,
    cpuCount: () => useAppState().state?.cpu_count ?? 8,

    // 参数合法性与 startState 均为派生值,后端 /api/state 仍为唯一事实源
    paramsValid(s) {
      const p = s.params;
      if (![2, 4].includes(p.scale)) return false;
      if (!Number.isInteger(p.target_width) || p.target_width < 0) return false;
      if (!MODELS.includes(p.model)) return false;
      if (!Number.isInteger(p.extract_workers)
          || p.extract_workers < 1
          || p.extract_workers > this.cpuCount) return false;
      return true;
    },

    // 状态机:IDLE(未选/非法) → READY → RUNNING。
    // DONE/ERROR 是任务终态,只由顶栏徽标展示,不占用按钮状态——
    // 结束后用户可换文件或直接重跑,故按钮回到 READY(文案在组件层区分「重新超分」)。
    startState(s) {
      if (this.running) return "running";
      return s.selectedFile && this.paramsValid && this.engineOk ? "ready" : "idle";
    },

    // 侧边栏 GPU 下拉选项:自动选择 + 后端自检枚举
    gpuOptions() {
      const gpus = useAppState().state?.gpus ?? [];
      return [
        { label: "自动选择", value: "" },
        ...gpus.map(([i, n]) => ({ label: `[${i}] ${n}`, value: String(i) })),
      ];
    },
  },

  actions: {
    // 两条通道(桌面壳路径 / 浏览器上传副本)统一收敛到这里。
    // busy 防连点:快速连选/连点「探测」时丢弃后到的并发请求。
    async selectByPath(path) {
      if (busy) return;
      busy = true;
      try {
        const info = await api.inspect(path);
        if (!info.exists) throw new Error("文件不存在或不是 PDF");
        this.selectedFile = path;
        this.fileInfo = info;
      } finally {
        busy = false;
      }
    },

    async selectFile(file) {
      const res = await api.upload(file);
      await this.selectByPath(res.path);
    },

    clearFile() {
      this.selectedFile = null;
      this.fileInfo = null;
    },

    // 开始任务。G1:成品已存在且开启跳过 → 确认「跳过 / 强制重做」。
    async start() {
      if (this.startState !== "ready") return;
      let skip = this.params.skip_existing;
      if (skip && this.fileInfo?.out_exists) {
        try {
          await ElMessageBox.confirm(
            "检测到同名成品已存在,如何处理?",
            "成品已存在",
            { confirmButtonText: "跳过(不重跑)", cancelButtonText: "强制重做",
              type: "warning", distinguishCancelAndClose: true });
          // 点「跳过」→ 保持 skip_existing=True,任务秒级 SKIP
        } catch (action) {
          // 点「强制重做」→ 关掉跳过,真实重跑
          if (action === "cancel") skip = false;
          else return;              // 点右上角关闭 → 放弃
        }
      }
      try {
        await api.createJob({ pdf: this.selectedFile, ...this.params, skip_existing: skip });
        this.view = "work";                    // [M3] 开始后自动切界面二
        ElMessage.success("任务已开始");
      } catch (e) {
        ElMessage.error(e.message);
      }
    },

    backToSelect() { this.view = "select"; },  // [M3] 界面二「更换文档」回界面一

    // [M3] 取消:确认后置位后端标志;canceled 后 startState 自动回 ready 可重跑
    async cancelJob() {
      const jobId = useAppState().state?.job_id;
      if (!this.running || !jobId) return;
      try {
        await ElMessageBox.confirm(
          "确定取消当前任务吗?正在进行的子进程将被终止。",
          "取消任务",
          { type: "warning", confirmButtonText: "取消任务", cancelButtonText: "再想想" });
      } catch { return; }
      try {
        await api.cancelJob(jobId);
        ElMessage.info("已请求取消,正在终止…");
      } catch (e) {
        ElMessage.error(e.message);
      }
    },
  },
});
