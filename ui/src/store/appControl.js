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
    selectedFile: null,            // 单选:PDF 文件或文件夹的绝对路径
    selectedPaths: null,           // 多选/多拖:路径数组(与 selectedFile 互斥)
    fileInfo: null,                // inspect(_many) 结果 {name, size, pages, count, out_path, ...}
    params: {
      scale: 4,
      target_width: 4320,
      model: "realesrgan-x4plus",
      gpu_ids: "",                // "" = 用后端自检的自动选择结果(后端 if not gpu_ids 回落)
      extract_workers: 8,          // 与 CLI 默认 min(8, cpu_count) 对齐;
                                   // 机器核数 < 8 时由 App.vue 首轮轮询后收敛(见 App.vue)
      skip_existing: true,
      recursive: false,            // 文件夹模式:是否递归子目录(仅选中文件夹时有意义)
    },
    // 输出位置(服务端 core/config 持久化;非任务参数,单独走 /api/config)
    outConfig: { out_mode: "fixed", out_dir: "", default_out_dir: "" },
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
      // 文件夹/多选里一个 PDF 都没有时不可开始(否则后端会 400)
      if ((s.fileInfo?.is_dir || s.fileInfo?.is_multi) && !s.fileInfo.count) return "idle";
      const hasInput = !!s.selectedFile || !!s.selectedPaths?.length;
      return hasInput && this.paramsValid && this.engineOk ? "ready" : "idle";
    },

    // 当前选中的是文件夹(批量模式)
    isDir: (s) => !!s.fileInfo?.is_dir,
    // 多选/多拖(批量模式)
    isMulti: (s) => !!s.fileInfo?.is_multi,
    // 输入里含文件夹 → 才需要展示「包含子文件夹」开关
    hasDirInput: (s) => !!s.fileInfo?.is_dir || (s.fileInfo?.dir_count ?? 0) > 0,

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
    // path 可以是 PDF 文件,也可以是文件夹(批量)。
    async selectByPath(path) {
      if (busy) return;
      busy = true;
      try {
        const info = await api.inspect(path, this.params.recursive);
        if (!info.exists) throw new Error("路径不存在,或不是 PDF 文件/文件夹");
        this.selectedFile = path;
        this.selectedPaths = null;      // 与多选互斥
        this.fileInfo = info;
      } finally {
        busy = false;
      }
    },

    // 多选/多拖:文件与文件夹混合,后端展开去重成 PDF 清单。
    // 只有一项时退化为单选(保留「文件夹」「单文件」的原有展示与语义)。
    async selectByPaths(paths) {
      const list = (paths || []).filter(Boolean);
      if (!list.length) return;
      if (list.length === 1) return this.selectByPath(list[0]);
      if (busy) return;
      busy = true;
      try {
        const info = await api.inspectMany(list, this.params.recursive);
        if (!info.exists) throw new Error("拖入的项目中没有 PDF 文件");
        this.selectedFile = null;
        this.selectedPaths = list;
        this.fileInfo = info;
      } finally {
        busy = false;
      }
    },

    async selectFile(file) {
      const res = await api.upload(file);
      await this.selectByPath(res.path);
    },

    // 重新探测当前选中文件(不改选择,仅刷新 fileInfo)。
    // 任务完成后成品才落盘,需借此把 out_exists 从旧的 false 更新为 true,
    // 驱动「成品:已存在」标签与「在文件夹中显示」按钮出现。失败静默忽略。
    async refreshFileInfo() {
      try {
        let info = null;
        if (this.selectedPaths?.length) {
          info = await api.inspectMany(this.selectedPaths, this.params.recursive);
        } else if (this.selectedFile) {
          info = await api.inspect(this.selectedFile, this.params.recursive);
        }
        if (info?.exists) this.fileInfo = info;
      } catch { /* 刷新失败不影响主流程 */ }
    },

    // ── 输出位置 ────────────────────────────────────────────
    async loadConfig() {
      try {
        this.outConfig = await api.getConfig();
      } catch { /* 读取失败保留默认展示,不阻断 */ }
    },

    // 更新输出位置并同步刷新 fileInfo(成品路径/已存在数会随之改变)
    async applyConfig(patch) {
      const cfg = await api.setConfig(patch);
      this.outConfig = cfg;
      await this.refreshFileInfo();
      return cfg;
    },

    clearFile() {
      this.selectedFile = null;
      this.selectedPaths = null;
      this.fileInfo = null;
    },

    // 开始任务。G1:成品已存在且开启跳过 → 确认「跳过 / 强制重做」。
    async start() {
      if (this.startState !== "ready") return;
      let skip = this.params.skip_existing;
      if (skip && this.fileInfo?.out_exists) {
        const info = this.fileInfo;
        const tip = (info.is_dir || info.is_multi)
          ? `所选 ${info.count} 个 PDF 中,有 ${info.existing_count} 个成品已存在,如何处理?`
          : "检测到同名成品已存在,如何处理?";
        try {
          await ElMessageBox.confirm(
            tip,
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
        const res = await api.createJob({
          ...(this.selectedPaths?.length
            ? { pdfs: this.selectedPaths }
            : { pdf: this.selectedFile }),
          ...this.params, skip_existing: skip });
        ElMessage.success(res.count > 1 ? `批量任务已开始(${res.count} 个 PDF)` : "任务已开始");
      } catch (e) {
        ElMessage.error(e.message);
      }
    },

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
