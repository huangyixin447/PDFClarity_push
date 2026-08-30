# PDFClarity

把**低分辨率 / 显示空白**的扫描版 PDF 一键变清晰。

针对「考研真题、老书扫描件」这类 PDF：每页是一整张图片，分辨率只有约 144 DPI，且使用 **JPEG2000 + 软蒙版**编码——部分阅读器（如 Skim、macOS 预览）渲染不出来，显示为空白页。

它会重新渲染每一页（去掉 JPEG2000，任何阅读器都能正常打开），再用 Real-ESRGAN（GPU 加速）逐页做 AI 超分，让字迹更锐利。

> 注意：144 DPI 的源图信息量有限，AI 只能「重建 / 补全」细节、改善观感，无法凭空恢复真实细节。要真正高清，最好拿到出版社的高分辨率原版。

---

## 快速开始

有三种入口：

| 入口 | 适合场景 | 启动命令 |
| ---- | ---- | ---- |
| **桌面版**（推荐） | 日常使用，原生窗口，拖拽选文件 | `python app.py` |
| **网页版** | 不想装 pywebview、远程访问 | `python app.py --server-only` |
| **命令行** | 批量处理、脚本化 | `python run.py <输入> [倍数] [宽度]` |

### 1. 安装依赖

```powershell
# 建议用 conda 或 venv 创建独立环境
python -m venv .venv
.venv\Scripts\activate

pip install pymupdf pillow tqdm psutil fastapi uvicorn python-multipart

# 仅桌面版需要（不装也不影响：会自动回退成网页版并打开浏览器）
pip install pywebview pythonnet
```

也可直接用仓库内的 `environment.yml` 创建 conda 环境：`conda env create -f environment.yml`。

环境要求：Python 3.10+、支持 Vulkan 的 GPU（NVIDIA / AMD / Intel）、Windows 10/11（自带 WebView2 运行时）。

### 2. 启动桌面版

```powershell
python app.py
```

启动后会先做一次 GPU 自检，稍等几秒弹出窗口：

- 点击文件区或把 PDF 拖进窗口选择文件（不复制，直接引用原文件）；
- 在右侧栏调整参数，点「开始」；
- 左侧逐行滚动处理进度，底部进度条平滑推进；
- 完成后点「在文件夹中显示」定位成品。

服务默认监听 `127.0.0.1:8000`，端口被占用时会自动顺延。

### 3. 打包版（可选）

随版本发布提供 `dist/PDFClarity.exe`（Windows 完整桌面程序，无需安装 Python）。双击即用；中间文件与成品存放在 `%LOCALAPPDATA%\PDFClarity\` 下（而非项目目录）。

---

## 使用说明

### 处理参数

| 参数 | 默认 | 说明 |
| ---- | ---- | ---- |
| 放大倍数 | 4 | Real-ESRGAN 放大倍数（2 / 4），4 质量最好 |
| 目标宽度 | 4320 | 成品每页宽度（px），约 288 DPI；0 = 保留完整超分尺寸 |
| 模型 | realesrgan-x4plus | 可换 `realesrgan-x4plus-anime` 等 |
| GPU | 自动选择 | 指定 ncnn GPU 编号（如 `0`、`0,1`）；默认自动优先独显（NVIDIA） |
| 提取进程数 | CPU 核数 | 拆分阶段的并行进程数，CPU 满载时可手动调小 |

成品输出到 `output/`；若同名成品已存在，开始时会询问「跳过还是重做」。

### 命令行（run.py）

```powershell
python run.py <输入.pdf 或 文件夹> [放大倍数] [目标宽度]
python run.py "真题方法篇.pdf"          # 处理单个 PDF
python run.py "D:\书籍\某文件夹"        # 批量处理文件夹下所有 PDF
python run.py "input.pdf" 4 0           # 4x 超分并保留完整尺寸
```

批量时某个 PDF 出错不会中断，其余继续处理，结束时汇总成功 / 失败数。

#### 环境变量

| 变量 | 默认 | 说明 |
| ---- | ---- | ---- |
| `MODEL` | `realesrgan-x4plus` | 超分模型 |
| `RECURSIVE` | `0` | 批量时设为 `1`，连子文件夹一起处理 |
| `SKIP_EXISTING` | `1` | 跳过已生成成品的 PDF（支持中断后续传）；设 `0` 强制重做 |
| `GPU_IDS` | 自动选择 | 指定 GPU 编号（如 `0` 或 `0,1`） |
| `EXTRA_ARGS` | 空 | 额外传给引擎的参数，如 `-j 1:2,2:2` |

---

## 工作原理

处理过程分三步：

```
输入 PDF
  │
  ▼
[1/3] 提取  scripts/extract_pages.py
  │          PyMuPDF 把每页渲染成 PNG（zoom 2.0，白底 RGB）
  │          顺带去掉 JPEG2000 / 软蒙版 → 解决「空白页」
  ▼
[2/3] 超分  realesrgan-ncnn-vulkan（GPU）
  │          对每页做 Real-ESRGAN AI 放大（x2 / x4），耗时约占 70%
  ▼
[3/3] 重建  scripts/rebuild_pdf.py
  │          超分图压缩回干净 PDF（JPEG 90，保留原始页面尺寸/旋转）
  ▼
成品 PDF（output/）
```

---

## 目录结构

```
PDFClarity/
├── app.py            # 桌面版 / 网页版入口
├── run.py            # 命令行入口
├── core/             # 核心逻辑
│   ├── paths.py      #   路径常量
│   ├── pipeline.py   #   三轮流水线调度
│   ├── gpu.py        #   引擎定位、GPU 选择
│   └── monitor.py    #   系统监控采样
├── scripts/          # 流水线两个子进程脚本
│   ├── extract_pages.py
│   └── rebuild_pdf.py
├── server/           # FastAPI 后端（/api/state、/api/jobs、/api/logs…）
├── ui/               # Vue3 前端（构建产物在 ui/dist/）
├── bin/realesrgan/   # 超分引擎 + 模型（已内置，勿改动）
└── environment.yml   # conda 环境定义
```

运行时自动生成 `work/`（中间文件，可随时删除）与 `output/`（成品 PDF），均已 git 忽略。

---

## 常见问题

- **首次运行被 SmartScreen / 杀软拦截**：打包版 exe 右键 → 属性 → 勾选「解除锁定」；杀软误报请加入信任区。
- **找不到 GPU / 超分报错**：确认已安装最新显卡驱动。注意 `nvidia-smi` 的编号与 Vulkan 枚举编号可能不一致，可手动用 `GPU_IDS` 指定。
- **长路径报错**：把项目放到短路径（如 `D:\pdfclarity`），或启用 Windows 的 `LongPathsEnabled`。
- **磁盘占用**：4x 超分的中间 PNG 较大，处理完删除 `work/` 即可回收（`clear.bat` 一键清理）。
- **电脑卡顿**：超分阶段 GPU 满载、拆分阶段多进程 CPU 打满属正常现象；可调小「提取进程数」。
- **8000 端口被占**：桌面版会自动顺延端口，无需处理。

---

## 开发者

```powershell
# 前端开发态（改 ui/ 代码时用，热更新）
cd ui
npm install
npm run dev          # → http://127.0.0.1:5173（/api 自动代理到后端）
# 同时另开终端跑 python app.py --server-only 提供后端

# 前端产物构建（改完前端后需重新 build，app.py 才显示新界面）
cd ui && npm run build
```
