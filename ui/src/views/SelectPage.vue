<template>
  <div class="select-page">
    <h3 class="page-title">选择 PDF 文档</h3>

    <!-- 通道一:点击 / 拖拽(浏览器模式,后端复制到 work/inbox/ 再处理) -->
    <el-upload class="dropzone" drag :auto-upload="false" :show-file-list="false"
               accept=".pdf" :on-change="onPick">
      <div class="dz-icon">PDF</div>
      <div class="el-upload__text">点击选择 或 拖拽 PDF 到此处</div>
      <template #tip>
        <div class="el-upload__tip">
          浏览器模式会把文件复制到 work/inbox/ 后处理（桌面版不经过此处，直接引用原文件）
        </div>
      </template>
    </el-upload>

    <!-- 通道二:粘贴绝对路径(与桌面壳同一条链路:selectByPath) -->
    <div class="path-row">
      <el-input v-model="pathInput" placeholder="或粘贴文件绝对路径后点「探测」(桌面版同通道)"
                clearable @keyup.enter="onProbe" />
      <el-button :disabled="!pathInput.trim() || probeBusy" :loading="probeBusy" @click="onProbe">
        探测
      </el-button>
    </div>

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
      <el-button size="small" text type="primary"
                 @click="control.clearFile(); pathInput.value = ''">更换文件</el-button>
    </div>
    <div v-else class="empty-box">
      <el-empty description="尚未选择文档" :image-size="90" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";
import { ElMessage } from "element-plus";
// 按需场景:JS API 需显式引样式(与 appControl.js 相同的处理方式)
import "element-plus/es/components/message/style/css";
import { useAppControl } from "../store/appControl";

const control = useAppControl();
const pathInput = ref("");
const probeBusy = ref(false);          // 探测中(大 PDF 页数统计可能耗时数百 ms~s 级)

const fileInfo = computed(() => control.fileInfo);
const sizeText = computed(() => {
  const n = control.fileInfo?.size;
  if (n == null) return "—";
  return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : (n / 1024).toFixed(0) + " KB";
});
const pagesText = computed(() => control.fileInfo?.pages ?? "未知（探测失败）");

// 通道一:点击 / 拖拽上传
function onPick(uploadFile) {
  // selectFile = 上传副本 -> selectByPath(inspect) 选中
  control.selectFile(uploadFile.raw)
    .catch((e) => ElMessage.error(e.message || "上传失败"));
}

// 通道二:粘贴绝对路径 -> inspect
async function onProbe() {
  const p = pathInput.value.trim();
  if (!p || probeBusy.value) return;
  probeBusy.value = true;
  try {
    await control.selectByPath(p);
  } catch (e) {
    ElMessage.error(e.message || "文件不存在或不是 PDF");
  } finally {
    probeBusy.value = false;
  }
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
.select-page { max-width: 720px; margin: 0 auto; padding: 24px 16px; }
.page-title { font-weight: 600; margin: 0 0 16px; }
.dropzone :deep(.el-upload-dragger) { padding: 36px 0; }
.dz-icon { font-size: 40px; font-weight: 700; color: #409eff; letter-spacing: 2px;
  margin-bottom: 8px; line-height: 1; }
.path-row { display: flex; gap: 8px; margin: 16px 0; }
.file-info { margin-top: 8px; }
.file-info .el-button { margin-top: 8px; }
.empty-box { margin-top: 8px; border: 1px dashed #e4e7ed; border-radius: 6px; }
</style>
