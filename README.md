# PDFClarity

把**低分辨率 / 在 Skim·预览里显示空白**的扫描版 PDF 变清晰。

针对的就是「考研真相 真题方法篇」这类问题:每页是一整张图片,分辨率只有 ~144 DPI,而且用的是 JPEG2000(JPXDecode)+ 软蒙版编码 —— Apple 的 PDFKit 渲染不出来,所以在 Skim/预览里是空白页。

本工具做两件事:
1. **修复空白**:重新渲染每页(去掉 JPEG2000),任何 PDF 阅读器都能正常打开。
2. **提清晰度**:用 Real-ESRGAN(Apple GPU 加速)对每页做 AI 超分,字边缘更锐利。

> 注意:144 DPI 的源图信息量有限,AI 只能"重建/补全"细节、改善观感,**无法凭空恢复真实细节**。要真正高清,最好拿到出版社的高分辨率原版。

## 环境准备(conda,只需一次)

推荐用 conda 环境运行。建环境 + 装包,任选一种:

```bash
# 方式 A:手动
conda create -n pdfclarity python=3.12 -y
conda activate pdfclarity
pip install pymupdf pillow tqdm

# 方式 B:用本项目的 environment.yml(等价)
conda env create -f environment.yml
```

需要三个 Python 包:`pymupdf`(PDF 读写/渲染)、`pillow`(图像缩放/JPEG 编码)、`tqdm`(进度条)。
超分工具 `bin/realesrgan/` 已自带,不走 Python,无需额外安装。
（`tqdm` 没装也能跑,只是没有 Python 那两步的进度条;超分步骤的进度条由 `run.sh` 自己画,不依赖它。）

## 用法

每次运行前先激活环境:

```bash
conda activate pdfclarity
cd /Users/wzf-perfomancemac/It_Development/PDFClarity
```

**处理单个 PDF:**
```bash
./run.sh "/Users/wzf-perfomancemac/Desktop/考研真相/真题方法篇 英语 (一）.原始备份.pdf"
```

**批量处理一个文件夹下的所有 PDF:**
```bash
./run.sh "/path/to/某个文件夹"
```
传入的是文件夹时,会自动找出其中所有 `.pdf` 逐个处理(中文名、带空格的名都支持)。

输出统一放在 `output/<原名>_clear.pdf`。
（`run.sh` 内部调用 `python3`,激活 conda 环境后它就指向该环境的 Python,无需改动。）

### 批量模式的可选开关(环境变量)

| 变量 | 默认 | 说明 |
|------|------|------|
| `RECURSIVE` | `0` | 设 `1` 则连**子文件夹**里的 PDF 也一起处理 |
| `SKIP_EXISTING` | `1` | 默认**跳过**已生成过成品的 PDF(可中断后续跑续传);设 `0` 强制重做 |

例:递归处理整个目录树,并强制全部重做:
```bash
RECURSIVE=1 SKIP_EXISTING=0 ./run.sh "/path/to/文件夹"
```

批量结束会汇总「成功/失败」数量;某个 PDF 出错不会中断,其余继续处理,最后列出失败文件。

### 参数(可选)

```bash
./run.sh <input.pdf> [model-scale] [target-width]
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `model-scale` | `4` | Real-ESRGAN 放大倍数(`2` 或 `4`)。`4` 质量最好 |
| `target-width` | `4320` | 最终每页宽度(像素)。4320 ≈ 288 DPI;设 `0` 保留完整放大尺寸 |

「4× 超分 → 缩到 288 DPI」是质量与体积的平衡点。想要极致清晰、不在乎体积,用:
```bash
./run.sh "input.pdf" 4 0
```

可选环境变量:`MODEL`(默认 `realesrgan-x4plus`,可换 `realesrgan-x4plus-anime`)、`PYTHON`。

## 依赖

- macOS(Apple Silicon GPU,通过 Vulkan/Metal)
- conda 环境 `pdfclarity`(见上面「环境准备」),内含 `pymupdf` + `pillow`
- Real-ESRGAN 二进制与模型已自带于 `bin/realesrgan/`(来自官方 xinntao/Real-ESRGAN v0.2.5.0)

## 目录结构

```
PDFClarity/
├── run.sh                 # 一键运行
├── scripts/
│   ├── extract_pages.py   # PDF → 每页 PNG(顺带去 JPEG2000)
│   └── rebuild_pdf.py     # 超分后的图 → 干净 PDF
├── bin/realesrgan/        # 自带的超分工具 + 模型
├── work/                  # 中间文件(可随时删除以释放空间)
└── output/                # 成品 PDF
```

## 耗时与磁盘

- 130 页在 M4 Pro 上约几分钟~十几分钟。
- 中间文件(4× PNG)较大,可能占几个 GB;处理完删除 `work/` 即可回收。

## 首次运行被 macOS 拦截?

若提示无法打开/开发者无法验证,执行一次:
```bash
xattr -dr com.apple.quarantine bin/realesrgan
```
(`run.sh` 已自动尝试,一般无需手动。)
