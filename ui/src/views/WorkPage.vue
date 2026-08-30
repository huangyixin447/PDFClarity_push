<template>
  <div class="work-page">
    <LogPanel class="panel" />
    <div class="bottom-area">
      <!-- 上行:用时 + 估算时间 + 取消 + 更换文档 -->
      <div class="meta-row">
        <span class="elapsed">用时 {{ elapsedText }}</span>
        <span class="eta">预计剩余 {{ etaText }}</span>
        <span class="spacer" />
        <el-button size="small" type="danger" plain
                   :disabled="!running" @click="control.cancelJob()">取消</el-button>
        <el-button size="small" @click="control.backToSelect()">更换文档</el-button>
      </div>
      <!-- 下行:加权进度条(中间文字 xx% : [类型] 具体工作) -->
      <el-progress class="bar" :percentage="percent" :stroke-width="28" text-inside
                   :status="barStatus" :format="fmt" />
    </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref } from "vue";
import { storeToRefs } from "pinia";
import { useAppState } from "../store/appState";
import { useAppControl } from "../store/appControl";
import LogPanel from "../components/LogPanel.vue";

const store = useAppState();
const control = useAppControl();
const { state, percent, running } = storeToRefs(store);

const fmt = (p) => `${p}% : ${state.value?.message || "等待中"}`;
const barStatus = computed(() => {
  const ph = state.value?.phase;
  if (ph === "done") return "success";
  if (ph === "error") return "exception";
  return "";
});

// 用时:运行中动态走秒;结束定格在 ended_at - started_at
const now = ref(Date.now());
const timer = setInterval(() => (now.value = Date.now()), 1000);
const elapsedText = computed(() => {
  const s = state.value;
  if (!s?.started_at) return "--:--";
  const end = s.running ? now.value / 1000 : (s.ended_at ?? s.started_at);
  const t = Math.max(0, Math.round(end - s.started_at));
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
});

// 估算剩余时间 —— 【算法留白】本轮只做显示 UI,占位 "--:--";
// 后续按真实任务数据测试后填入,候选公式:
//   eta = (now - started_at) / percent * (100 - percent)  (加权进度均匀假设)
const etaText = computed(() => "--:--");
onUnmounted(() => clearInterval(timer));
</script>

<style scoped>
.work-page { height: 100%; display: flex; flex-direction: column; }
.panel { flex: 1; min-height: 0; }
.bottom-area { flex-shrink: 0; padding: 10px 16px; border-top: 1px solid #ebeef5;
  background: #fff; }
.meta-row { display: flex; align-items: center; gap: 16px; margin-bottom: 8px;
  font-size: 12px; color: #909399; }
.spacer { flex: 1; }
.bar { width: 100%; }
/* 进度条中间文字过长时省略号防溢出(需 :deep 穿透) */
.work-page :deep(.el-progress__text) {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
</style>
