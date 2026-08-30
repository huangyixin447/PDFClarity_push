# PDFClarity

把**低分辨率 / 在 Skim·预览里显示空白**的扫描版 PDF 变清晰。

针对「考研真相 真题方法篇」这类 PDF：每页是一整张图片，分辨率约 144 DPI，且使用 JPEG2000（JPXDecode）+ 软蒙版编码 —— Apple 的 PDFKit 渲染不出来，所以在 Skim / 预览里显示空白页。

本工具做两件事：

1. **修复空白**：重新渲染每页（去掉 JPEG2000），任何 PDF 阅读器都能正常打开。
2. **提清晰度**：用 Real-ESRGAN（GPU 加速）对每页做 AI 超分，字边缘更锐利。

> 注意：144 DPI 的源图信息量有限，AI 只能「重建/补全」细节、改善观感，**无法凭空恢复真实细节**。要真正高清，最好拿到出版社的高分辨率原版。

## 环境要求

- Python 3.10+，依赖 `pymupdf`、`pillow`、`tqdm`
- 支持 Vulkan 的 GPU（NVIDIA / AMD / Intel / Apple Silicon）
- 超分引擎已内置在 `bin/realesrgan/`，无需单独安装

## 安装

任选其一创建 Python 环境（只需一次）：

```bash
# 方式 A：conda
conda create -n pdfclarity python=3.12 -y
conda activate pdfclarity
pip install pymupdf pillow tqdm
```

```powershell
# 方式 B：Windows 虚拟环境
python -m venv .venv
.venv\Scripts\activate
pip install pymupdf pillow tqdm
```

## 用法

```powershell
# Windows
python run.py <输入.pdf 或 文件夹> [放大倍数] [目标宽度]

# macOS / Git Bash（run.py 跨平台，macOS 下用 python3；启动时自动处理 quarantine 放行）
python3 run.py <输入.pdf 或 文件夹> [放大倍数] [目标宽度]
```

示例：

```powershell
python run.py "真题方法篇.pdf"          # 处理单个 PDF
python run.py "D:\书籍\某文件夹"         # 批量处理文件夹下所有 PDF
python run.py "input.pdf" 4 0           # 4x 超分并保留完整尺寸
```

输出统一放在 `output/<原名>_clear.pdf`。

### 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `放大倍数` | `4` | Real-ESRGAN 放大倍数（`2` 或 `4`），`4` 质量最好 |
| `目标宽度` | `4320` | 最终每页宽度（px），约 288 DPI；`0` = 保留完整超分尺寸 |

### 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `MODEL` | `realesrgan-x4plus` | 超分模型，可换 `realesrgan-x4plus-anime` 等 |
| `RECURSIVE` | `0` | 批量时设为 `1`，连子文件夹里的 PDF 一起处理 |
| `SKIP_EXISTING` | `1` | 默认跳过已生成成品的 PDF（支持中断后续传）；设 `0` 强制重做 |
| `GPU_IDS` | 自动选择 | 指定 ncnn GPU 编号（如 `0` 或 `0,1`）；不设时自动优先独显（NVIDIA） |
| `EXTRA_ARGS` | 空 | 额外传给引擎的参数，如 `-j 1:2,2:2` |

批量结束时汇总「成功/失败」数量；某个 PDF 出错不会中断，其余继续处理。

## 目录结构

```
PDFClarity/
├── run.py                 # 主入口（Windows / macOS / Git Bash 通用）
├── scripts/
│   ├── extract_pages.py   # PDF → 每页 PNG（顺带去 JPEG2000）
│   └── rebuild_pdf.py     # 超分后的图 → 干净 PDF
├── bin/realesrgan/        # 超分引擎 + 模型
├── input/                 # 示例输入
├── work/                  # 中间文件（运行时生成，可随时删除）
└── output/                # 成品 PDF
```

## 常见问题

- **Windows SmartScreen 拦截**：右键 `bin/realesrgan/realesrgan-ncnn-vulkan.exe` → 属性 → 勾选「解除锁定」。
- **macOS 首次运行被拦截**：run.py 启动时会自动清除引擎的 quarantine 属性并设置可执行位；若仍被拦截，可手动执行一次 `xattr -dr com.apple.quarantine bin/realesrgan`。
- **找不到 GPU / 超分报错**：确认已安装最新显卡驱动。注意 `nvidia-smi` 的编号与 Vulkan 枚举编号可能不一致；可手动用 `GPU_IDS` 指定（如 `GPU_IDS=1`）。
- **长路径报错**：把项目放到短路径（如 `D:\pdfclarity`），或启用 Windows 的 LongPathsEnabled。
- **磁盘占用**：4x 超分的中间 PNG 较大；处理完删除 `work/` 即可回收空间。

## 性能参考

- 130 页在 M4 Pro 上约几分钟~十几分钟。
- 双显卡（核显 + 独显）笔记本上会自动优先使用独显；可用 `GPU_IDS` 手动指定。
