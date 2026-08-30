<template>
  <div class="log-panel">
    <div class="head">
      <el-tabs v-model="tab" size="small" class="tabs">
        <el-tab-pane v-for="f in LOG_FILES" :key="f" :name="f">
          <template #label>
            {{ LABELS[f] }}
            <i v-if="isCurrent(f)" class="dot" title="当前阶段" />
            <span v-if="store.logsTruncated[f]" class="warn" title="日志过大,已截断最早部分">⚠</span>
          </template>
        </el-tab-pane>
      </el-tabs>
      <el-button size="small" link type="primary" @click="copyAll">复制全部</el-button>
    </div>
    <div ref="box" class="box" @scroll="onScroll">
      <div v-for="(ln, i) in store.logs[tab]" :key="i"
           class="line" :class="{ err: /error|traceback|failed/i.test(ln) }">{{ ln }}</div>
      <div v-if="!follow" class="jump" @click="jumpBottom">▼ 回到底部(已暂停跟随)</div>
    </div>
  </div>
</template>

<script setup>
import { nextTick, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { useAppState } from "../store/appState";

const LOG_FILES = ["extract", "upscale", "rebuild"];
const LABELS = { extract: "提取", upscale: "超分", rebuild: "重建" };

const store = useAppState();
const tab = ref("extract");
const box = ref(null);
// 跟随 = 「最后一条日志可见」。初始为 true(贴底);滚动事件实时判定。
const follow = ref(true);

const isCurrent = (f) => store.state?.phase === f && store.running;

// 行数变化 → 仅当最后一条可见(跟随)时才自动下滚
watch(() => store.logs[tab.value]?.length, async () => {
  if (follow.value) {
    await nextTick();
    const el = box.value;
    if (el) el.scrollTop = el.scrollHeight;
  }
});

// 最后一条可见判定:距底部 <= 4px 视为贴底(最后一条被显示);
// 用户上翻立即不满足 → follow=false 停止自动滚动;滚回底部 → 恢复
function onScroll() {
  const el = box.value;
  if (!el) return;
  follow.value = el.scrollHeight - el.scrollTop - el.clientHeight <= 4;
}
function jumpBottom() {
  const el = box.value;
  if (el) el.scrollTop = el.scrollHeight;
}

async function copyAll() {
  const lines = store.logs[tab.value];
  await navigator.clipboard.writeText(lines.join("\n")).catch(() => {});
  ElMessage.success(`已复制 ${lines.length} 行`);
}
</script>

<style scoped>
.log-panel { height: 100%; display: flex; flex-direction: column; padding: 8px 16px 0; }
.head { flex-shrink: 0; display: flex; align-items: center; justify-content: space-between; }
.tabs { flex: 1; }
.dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%;
  background: #409eff; margin-left: 4px; vertical-align: middle; }
.warn { margin-left: 4px; font-size: 12px; }
.box { flex: 1; min-height: 0; overflow-y: auto; margin-top: 4px;
  background: #1e1e1e; color: #d4d4d4; border-radius: 4px;
  font-family: Consolas, "Courier New", monospace; font-size: 12px; line-height: 1.7;
  padding: 8px 12px; }
.line { white-space: pre-wrap; word-break: break-all; }
.line.err { color: #f56c6c; }
.jump { position: sticky; bottom: 8px; text-align: center; cursor: pointer;
  color: #409eff; background: rgba(30, 30, 30, 0.9); padding: 4px; }
</style>
