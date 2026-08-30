<template>
  <div class="app-root">
    <header class="topbar">
      <span class="brand">PDFClarity</span>
      <span class="sub">低清 PDF 一键变清晰</span>
      <span class="spacer" />
      <el-tag :type="phaseTag.type" size="small" effect="dark">{{ phaseTag.text }}</el-tag>
      <span class="conn" :class="connClass">
        <i class="dot" />{{ connText }}
      </span>
    </header>

    <div class="body">
      <main class="main">
        <SelectPage />
      </main>
      <Sidebar />
    </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, watch } from "vue";
import { storeToRefs } from "pinia";
import { useAppState } from "./store/appState";
import { useAppControl } from "./store/appControl";
import Sidebar from "./components/Sidebar.vue";
import SelectPage from "./views/SelectPage.vue";

const store = useAppState();
const control = useAppControl();
store.start();                               // 轮询生命周期:App 挂载即启动
const { state, pollError } = storeToRefs(store);

// 机器核数 < 8 时,把默认提取线程数收敛到实际核数(否则 paramsValid 永远为 false)
watch(() => state.value?.cpu_count, (n) => {
  if (n && control.params.extract_workers > n) control.params.extract_workers = n;
});

// 顶栏任务状态徽标:直接消费轮询快照的 phase
const PHASES = {
  idle:    { text: "就绪",   type: "info" },
  extract: { text: "提取中", type: "warning" },
  upscale: { text: "超分中", type: "primary" },
  rebuild: { text: "重建中", type: "warning" },
  done:    { text: "已完成", type: "success" },
  error:   { text: "失败",   type: "danger" },
  canceled:{ text: "已取消", type: "info" },    // [M3]
};
const phaseTag = computed(() => PHASES[state.value?.phase] ?? PHASES.idle);

// 连接状态:轮询连续失败(指数退避)时 pollError 非空
const connText = computed(() => (pollError.value ? "连接中断" : "后端已连接"));
const connClass = computed(() => (pollError.value ? "bad" : "ok"));

onUnmounted(() => store.stop());
</script>

<style scoped>
.app-root { height: 100vh; display: flex; flex-direction: column; }
.topbar { height: 48px; display: flex; align-items: center; gap: 12px; padding: 0 16px;
  background: #001529; color: #fff; flex-shrink: 0; }
.brand { font-size: 16px; font-weight: 700; letter-spacing: 1px; }
.sub { font-size: 12px; color: #8c9aa8; }
.spacer { flex: 1; }
.conn { font-size: 12px; display: inline-flex; align-items: center; gap: 6px; }
.conn .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.conn.ok .dot { background: #67c23a; }
.conn.ok { color: #b8e6b0; }
.conn.bad .dot { background: #f56c6c; }
.conn.bad { color: #f89898; }
.body { flex: 1; display: flex; min-height: 0; }
.main { flex: 1; overflow-y: auto; background: #fff; }
</style>
