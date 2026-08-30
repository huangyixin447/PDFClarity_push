<template>
  <div class="select-page">
    <!-- 上下结构:上 = 左右两栏,下 = 底部进度条 -->
    <div class="cols">
      <!-- 左栏:处理状态(终端式,一行行输出并自动滚动,类似安装包) -->
      <section class="col col-status">
        <h3 class="page-title">处理状态</h3>
        <div ref="consoleBox" class="console">
          <div v-if="!consoleLines.length" class="empty">尚未开始任务</div>
          <div v-for="(ln, i) in consoleLines" :key="i" class="line" :class="ln.cls">
            {{ ln.text }}
          </div>
        </div>
        <div v-if="running" class="lock-tip">任务运行中，右侧文件选择已锁定</div>
      </section>

      <!-- 右栏:原文件选择 UI(运行中锁定不可交互) -->
      <section class="col col-file">
        <h3 class="page-title">选择 PDF 文档</h3>

        <!-- 点击 / 拖拽选择 PDF -->
        <el-upload class="dropzone" drag :auto-upload="false" :show-file-list="false"
                   accept=".pdf" :disabled="running" :on-change="onPick">
          <div class="dz-icon">PDF</div>
          <div class="el-upload__text">点击选择 或 拖拽 PDF 到此处</div>
        </el-upload>

        <!-- 选中文件信息 -->
        <div v-if="fileInfo" class="file-info">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="文件名" :span="2">{{ fileInfo.name }}</el-descriptions-item>
            <el-descriptions-item label="大小">{{ sizeText }}</el-descriptions-item>
            <el-descriptions-item label="页数">{{ pagesText }}</el-descriptions-item>
            <el-descriptions-item label="成品" :span="2">
              <el-tag v-if="fileInfo.out_exists" type="warning" size="small">
                已存在（开始时会询问是跳过还是重做）
              </el-tag>
              <el-tag v-else type="success" size="small">尚未生成</el-tag>
            </el-descriptions-item>
          </el-descriptions>
          <el-button size="small" text type="primary" :disabled="running"
                     @click="control.clearFile()">更换文件</el-button>
        </div>
        <div v-else class="empty-box">
          <el-empty description="尚未选择文档" :image-size="90" />
        </div>
      </section>
    </div>

    <!-- 底部进度条:显示值经前端缓动(指数逼近,单调不减)平滑推进 -->
    <div class="bottom-area">
      <el-progress class="bar" :percentage="displayPercent" :stroke-width="28" text-inside
                   :status="barStatus" :format="fmt" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted, watch } from "vue";
import { storeToRefs } from "pinia";
import { ElMessage } from "element-plus";
// 按需场景:JS API 需显式引样式(与 appControl.js 相同的处理方式)
import "element-plus/es/components/message/style/css";
import { useAppState } from "../store/appState";
import { useAppControl } from "../store/appControl";

const store = useAppState();
const control = useAppControl();
const { state, running, phase, percent } = storeToRefs(store);

const fileInfo = computed(() => control.fileInfo);
const sizeText = computed(() => {
  const n = control.fileInfo?.size;
  if (n == null) return "—";
  return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : (n / 1024).toFixed(0) + " KB";
});
const pagesText = computed(() => control.fileInfo?.pages ?? "未知（探测失败）");

// ── 左栏:终端式状态输出(阶段切换/每完成一页追加一行,自动滚动到底) ──
const consoleLines = ref([]);        // [{text, cls}] cls ∈ {"" , "ok", "err"}
const lastPage = ref(0);             // 当前阶段已推进到的页数,用于页推进去重
const consoleBox = ref(null);

function pushLine(text, cls = "") {
  consoleLines.value.push({ text, cls });
  if (consoleLines.value.length > 1000) {          // 行数上限,防止长任务撑爆内存
    consoleLines.value.splice(0, consoleLines.value.length - 1000);
  }
}

// 行数变化 → 自动滚动到底部(像安装包一样持续下滚)
watch(() => consoleLines.value.length, async () => {
  await nextTick();
  const el = consoleBox.value;
  if (el) el.scrollTop = el.scrollHeight;
});

// 新任务(job_id 变化)→ 清空并输出起始行
watch(() => state.value?.job_id, (id, oldId) => {
  if (id && id !== oldId) {
    consoleLines.value = [];
    lastPage.value = 0;
    const name = state.value?.pdf ? state.value.pdf.split(/[\\/]/).pop() : "";
    pushLine(name ? `开始处理：${name}` : "开始处理…");
  }
});

// 阶段切换 → 输出一行(同时把页计数器归零,因为各阶段页码各自从 0 起)
watch(phase, (ph, old) => {
  if (ph === old) return;
  lastPage.value = 0;
  if (ph === "extract") pushLine("正在拆分页面…");
  else if (ph === "upscale") pushLine("正在超分…");
  else if (ph === "rebuild") pushLine("正在重建 PDF…");
  else if (ph === "done") pushLine("任务完成", "ok");
  else if (ph === "error") pushLine(`任务失败：${state.value?.error || "未知错误"}`, "err");
  else if (ph === "canceled") pushLine("任务已取消", "err");
});

// 页推进 → 每完成一页追加一行「已完成xxx x/y 页」
watch(() => state.value?.page, (p) => {
  const total = state.value?.total;
  if (typeof p !== "number" || p <= lastPage.value) return;
  lastPage.value = p;
  if (total) {
    if (phase.value === "extract") pushLine(`已完成拆分 ${p}/${total} 页`);
    else if (phase.value === "upscale") pushLine(`已完成超分 ${p}/${total} 页`);
    else if (phase.value === "rebuild") pushLine(`已完成重建 ${p}/${total} 页`);
  } else if (phase.value === "extract" || phase.value === "upscale") {
    pushLine(`进度：已完成 ${p} 页`);
  }
});

// ── 底部进度条:显示值缓动(不直接渲染真实 percent,消除页数少时的大段瞬跳) ──
const displayPct = ref(0);
const displayPercent = computed(() => Math.round(displayPct.value));
const fmt = (p) => `${p}%`;
const barStatus = computed(() => {
  if (phase.value === "done") return "success";
  if (phase.value === "error") return "exception";
  return "";
});

let smoothTimer = null;
// 新任务(job_id 变化)→ 显示进度归零重来
watch(() => state.value?.job_id, () => { displayPct.value = 0; });
// 终态直接锚定:完成满格、失败/取消定格,避免任务结束后进度条还在慢吞吞滑动
watch(phase, (ph) => {
  if (ph === "done") displayPct.value = 100;
  else if (ph === "error" || ph === "canceled") displayPct.value = percent.value;
});

onMounted(() => {
  // 每 200ms 向真实百分比指数逼近(单调不减);大步差距走快、小步走慢,
  // 保证页数少时进度条也呈连续滑动而非瞬跳。
  smoothTimer = setInterval(() => {
    const target = percent.value;
    if (displayPct.value < target) {
      const step = Math.max(1, (target - displayPct.value) * 0.12);
      displayPct.value = Math.min(target, displayPct.value + step);
    }
  }, 200);
});
onUnmounted(() => clearInterval(smoothTimer));

// 点击 / 拖拽上传
function onPick(uploadFile) {
  // selectFile = 上传副本 -> selectByPath(inspect) 选中
  control.selectFile(uploadFile.raw)
    .catch((e) => ElMessage.error(e.message || "上传失败"));
}

// 阻止浏览器默认「拖放打开文件」:拖到窗口空白处会直接跳转打开 PDF
const preventDrop = (e) => e.preventDefault();
onMounted(() => {
  window.addEventListener("dragover", preventDrop);
  window.addEventListener("drop", preventDrop);
});
onUnmounted(() => {
  window.removeEventListener("dragover", preventDrop);
  window.removeEventListener("drop", preventDrop);
});
</script>

<style scoped>
.select-page { height: 100%; display: flex; flex-direction: column; }
.cols { flex: 1; display: flex; min-height: 0; }
.col { min-width: 0; }
/* 左栏:处理状态(终端式) */
.col-status { flex: 1; padding: 24px 16px; border-right: 1px dashed #e4e7ed;
  display: flex; flex-direction: column; min-height: 0; }
.page-title { font-weight: 600; margin: 0 0 16px; }
.console { flex: 1; min-height: 0; overflow-y: auto; background: #1e1e1e; color: #d4d4d4;
  border-radius: 6px; font-family: Consolas, "Courier New", monospace; font-size: 12px;
  line-height: 1.8; padding: 10px 12px; }
.console .line { white-space: pre-wrap; word-break: break-all; }
.console .line.ok { color: #67c23a; }
.console .line.err { color: #f56c6c; }
.console .empty { color: #6b6b6b; }
.lock-tip { margin-top: 12px; font-size: 12px; color: #e6a23c; }
/* 右栏:文件选择 */
.col-file { flex: 1; max-width: 720px; padding: 24px 16px; }
.dropzone :deep(.el-upload-dragger) { padding: 36px 0; }
.dz-icon { font-size: 40px; font-weight: 700; color: #409eff; letter-spacing: 2px;
  margin-bottom: 8px; line-height: 1; }
.file-info { margin-top: 8px; }
.file-info .el-button { margin-top: 8px; }
.empty-box { margin-top: 8px; border: 1px dashed #e4e7ed; border-radius: 6px; }
/* 底部进度条 */
.bottom-area { flex-shrink: 0; padding: 10px 16px; border-top: 1px solid #ebeef5;
  background: #fff; }
.bar { width: 100%; }
</style>
