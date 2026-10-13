<template>
  <aside class="sidebar">
    <!-- ── 系统监控 ─────────────────────────────────────── -->
    <el-card shadow="never" class="sec">
      <template #header>
        <div class="sec-head">
          <span>系统监控</span>
          <el-tag size="small" type="info" effect="plain">
            峰值 CPU {{ peakCpuText }} · GPU {{ peakGpuText }}
          </el-tag>
        </div>
      </template>

      <div class="mon-row">
        <span class="mon-label">CPU</span>
        <el-progress class="mon-bar" :percentage="cpuNum" :stroke-width="8"
                     :show-text="false" :color="barColor" />
        <span class="mon-val">{{ cpuText }}</span>
      </div>
      <div class="mon-row">
        <span class="mon-label">内存</span>
        <el-progress class="mon-bar" :percentage="ramNum" :stroke-width="8"
                     :show-text="false" :color="barColor" />
        <span class="mon-val">{{ ramText }}</span>
      </div>

      <template v-if="gpuCards.length">
        <div v-for="g in gpuCards" :key="g.idx" class="gpu-card">
          <div class="gpu-head">
            <span>GPU {{ g.idx }}</span>
            <span class="gpu-util">{{ g.util }}%</span>
          </div>
          <el-progress :percentage="g.util" :stroke-width="6" :show-text="false" :color="barColor" />
          <div class="gpu-meta">{{ g.metaText }}</div>
        </div>
      </template>
      <div v-else class="gpu-na">GPU n/a（未检测到 nvidia-smi）</div>
    </el-card>

    <!-- ── 参数设置 ─────────────────────────────────────── -->
    <el-card shadow="never" class="sec">
      <template #header><span>参数设置</span></template>
      <el-form :model="params" label-position="top" size="small"
               :disabled="running" class="params-form">
        <el-form-item label="放大倍数">
          <el-radio-group v-model="params.scale">
            <el-radio-button :value="2">2x</el-radio-button>
            <el-radio-button :value="4">4x（默认）</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="目标宽度 px（0 = 完整尺寸）">
          <el-input-number v-model="params.target_width" :min="0" :max="20000"
                           :step="100" controls-position="right" class="full" />
        </el-form-item>
        <el-form-item label="模型">
          <el-select v-model="params.model" class="full">
            <el-option v-for="m in MODELS" :key="m" :label="m" :value="m" />
          </el-select>
        </el-form-item>
        <el-form-item label="GPU">
          <el-select v-model="params.gpu_ids" class="full" placeholder="自动选择">
            <el-option v-for="o in gpuOptions" :key="String(o.value)"
                       :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item :label="`提取线程数（1 ~ ${cpuCount}）`">
          <el-input-number v-model="params.extract_workers" :min="1" :max="cpuCount"
                           controls-position="right" class="full" />
        </el-form-item>
        <el-form-item label="成品已存在时跳过">
          <el-switch v-model="params.skip_existing" />
        </el-form-item>
        <el-form-item v-if="hasDirInput" label="包含子文件夹">
          <el-switch v-model="params.recursive" />
          <span class="hint-inline">递归扫描子目录中的 PDF</span>
        </el-form-item>

        <el-form-item label="输出位置">
          <el-radio-group :model-value="outConfig.out_mode" @change="onModeChange">
            <el-radio-button value="fixed">指定目录</el-radio-button>
            <el-radio-button value="source">源 PDF 同目录</el-radio-button>
          </el-radio-group>
          <div v-if="outConfig.out_mode === 'source'" class="out-note">
            每个成品写到它自己源文件所在的文件夹（批量时不会同名互相覆盖）
          </div>
          <template v-else>
            <div class="out-path" :title="outConfig.out_dir">{{ outConfig.out_dir }}</div>
            <div class="out-actions">
              <el-button v-if="isDesktop" size="small" @click="pickOutDir">选择目录…</el-button>
              <el-button size="small" text type="primary"
                         :disabled="outConfig.out_dir === outConfig.default_out_dir"
                         @click="resetOutDir">恢复默认</el-button>
            </div>
          </template>
        </el-form-item>
      </el-form>
      <div v-if="running" class="lock-tip">任务运行中，参数已锁定</div>
    </el-card>

    <!-- ── 开始 / 停止按钮(运行时切换为「停止任务」) ─────────── -->
    <div class="footer">
      <el-button class="start-btn" size="large"
                 :type="running ? 'danger' : 'primary'"
                 :disabled="!running && startState === 'idle'"
                 @click="running ? control.cancelJob() : control.start()">
        {{ running ? "停止任务" : startText }}
      </el-button>
      <div class="start-hint">{{ startHint }}</div>
    </div>
  </aside>
</template>

<script setup>
import { computed, onUnmounted, ref } from "vue";
import { storeToRefs } from "pinia";
import { ElMessage } from "element-plus";
import "element-plus/es/components/message/style/css";
import { useAppState } from "../store/appState";
import { useAppControl } from "../store/appControl";

// 与 appControl.js 的校验列表保持一致(2 个模型,数量少,允许小量重复)
const MODELS = ["realesrgan-x4plus", "realesrgan-x4plus-anime"];

const store = useAppState();
const control = useAppControl();
const { state, running, phase } = storeToRefs(store);
const { params, gpuOptions, startState, cpuCount, isDir, isMulti, hasDirInput,
        outConfig } = storeToRefs(control);

// ── 输出位置 ────────────────────────────────────────────
control.loadConfig();

// 桌面壳才有原生目录对话框(api 异步注入,故需响应式,见 SelectPage.vue 同款处理)
const isDesktop = ref(!!window.pywebview?.api);
if (!isDesktop.value) {
  const mark = () => { isDesktop.value = !!window.pywebview?.api; return isDesktop.value; };
  window.addEventListener("pywebviewready", mark, { once: true });
  let tries = 0;
  const timer = setInterval(() => { if (mark() || ++tries > 25) clearInterval(timer); }, 200);
  onUnmounted(() => clearInterval(timer));
}

async function apply(patch, okMsg) {
  try {
    await control.applyConfig(patch);
    if (okMsg) ElMessage.success(okMsg);
  } catch (e) {
    ElMessage.error(e.message || "设置失败");
    await control.loadConfig();       // 失败回读,保持界面与服务端一致
  }
}

const onModeChange = (mode) => apply({ out_mode: mode });
const resetOutDir = () => apply({ out_dir: outConfig.value.default_out_dir }, "已恢复默认输出目录");

async function pickOutDir() {
  try {
    const dir = await window.pywebview.api.select_folder();
    if (dir) await apply({ out_dir: dir }, "输出目录已更新");
  } catch (e) {
    ElMessage.error(e.message || "选择目录失败");
  }
}

// ── 监控区(数据来自 /api/state.metrics,随轮询更新) ──
const metrics = computed(() => state.value?.metrics ?? {});
const cpuNum = computed(() => Math.round(metrics.value.cpu ?? 0));
const cpuText = computed(() => (metrics.value.cpu == null ? "n/a" : cpuNum.value + "%"));
const ramNum = computed(() => Math.round(metrics.value.ram ?? 0));
const ramText = computed(() => (metrics.value.ram == null ? "n/a" : ramNum.value + "%"));
const peakCpuText = computed(() => (metrics.value.peaks?.cpu ?? 0).toFixed(0) + "%");
const peakGpuText = computed(() => (metrics.value.peaks?.gpu ?? 0).toFixed(0) + "%");

const gpuCards = computed(() =>
  Object.entries(metrics.value.gpu ?? {}).map(([idx, g]) => ({
    idx,
    util: Math.round(g.util ?? 0),
    metaText:
      `${Math.round(g.mem_used ?? 0)} / ${Math.round(g.mem_total ?? 0)} MiB` +
      (g.temp != null ? ` · ${g.temp.toFixed(0)}°C` : "") +
      (g.power != null ? ` · ${g.power.toFixed(0)}W` : ""),
  }))
);

// 占用率颜色:绿 <50 < 蓝 <80 < 橙
function barColor(v) {
  return v >= 80 ? "#e6a23c" : v >= 50 ? "#409eff" : "#67c23a";
}

// ── 开始 / 停止按钮(状态机见 M2 方案 §5.4;运行时切换为「停止任务」) ──
const finished = computed(() => phase.value === "done" || phase.value === "error");
const pdfCount = computed(() => control.fileInfo?.count ?? 0);
const isBatch = computed(() => isDir.value || isMulti.value);
const startText = computed(() => {
  if (finished.value) return "重新超分";
  return isBatch.value && pdfCount.value > 1 ? `开始超分（${pdfCount.value} 个）` : "开始超分";
});
const startHint = computed(() => {
  if (running.value) {
    const st = state.value || {};
    return st.batch_total > 1
      ? `批量进行中：第 ${st.batch_index}/${st.batch_total} 个，点击「停止任务」可终止`
      : "任务运行中，点击「停止任务」可终止";
  }
  if (startState.value === "idle") {
    if (!control.selectedFile && !control.selectedPaths?.length) {
      return "请先选择 PDF 文档或文件夹（可拖入多个）";
    }
    if (isBatch.value && !pdfCount.value) return "没有找到 PDF，可开启「包含子文件夹」";
    if (!control.paramsValid) return "参数不合法，请检查设置";
    if (!control.engineOk) return "超分引擎缺失，请检查 bin/realesrgan/";
  }
  if (finished.value) {
    return `上一任务已${phase.value === "done" ? "完成" : "失败"}，可再次开始`;
  }
  return "就绪，点击开始超分";
});
</script>

<style scoped>
.sidebar {
  width: 320px; padding: 12px; display: flex; flex-direction: column; gap: 12px;
  overflow-y: auto; background: #f5f7fa; border-left: 1px solid #e4e7ed; flex-shrink: 0;
}
.sec { border: none; }
.sec-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.mon-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.mon-label { width: 40px; color: #606266; font-size: 13px; flex-shrink: 0; }
.mon-bar { flex: 1; }
.mon-val { width: 44px; text-align: right; color: #303133; font-size: 12px;
  font-variant-numeric: tabular-nums; }
.gpu-card { border: 1px solid #e4e7ed; border-radius: 6px; padding: 8px; margin-top: 8px; }
.gpu-head { display: flex; justify-content: space-between; margin-bottom: 6px; font-size: 13px; }
.gpu-meta { margin-top: 6px; font-size: 12px; color: #909399; }
.gpu-na { color: #909399; font-size: 12px; padding: 8px 0; }
.params-form :deep(.el-form-item) { margin-bottom: 14px; }
.full { width: 100%; }
.lock-tip { color: #e6a23c; font-size: 12px; padding: 4px 0; }
.hint-inline { margin-left: 8px; font-size: 12px; color: #909399; }
.out-note { margin-top: 6px; font-size: 12px; color: #909399; line-height: 1.5; }
.out-path { margin-top: 6px; width: 100%; font-size: 12px; color: #606266;
  font-family: Consolas, "Courier New", monospace; word-break: break-all;
  line-height: 1.5; background: #f5f7fa; border-radius: 4px; padding: 4px 6px; }
.out-actions { margin-top: 6px; display: flex; gap: 8px; align-items: center; }
.footer { margin-top: auto; }
.start-btn { width: 100%; }
.start-hint { margin-top: 6px; font-size: 12px; color: #909399; text-align: center; }
</style>
