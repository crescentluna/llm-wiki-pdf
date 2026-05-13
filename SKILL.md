---
name: llm-wiki-pdf
description: 初始化和维护 Karpathy 风格的 LLM-Wiki 学术论文知识库。将 PDF 论文提取为结构化 Markdown 并进行图片映射。支持双提取后端（MinerU VLM API 优先，pymupdf4llm 备选）。当用户提到 LLM-Wiki、论文知识库、PDF 提取、Karpathy wiki，或希望从学术论文构建结构化知识库时使用。
author:
  empId: "85571"
---

# LLM-Wiki PDF 提取

从学术 PDF 论文构建和维护 Karpathy 风格的 LLM-Wiki 知识库。

## 概述

本 Skill 提供：
1. **PDF 转 Markdown 提取**，支持双后端（MinerU VLM API / pymupdf4llm）
2. **图片提取**，带启发式 Figure 编号映射
3. **AGENTS.md 模板**，用于初始化新的 LLM-Wiki 项目

## 快速开始

### 初始化新的 LLM-Wiki 项目

1. 将脚本复制到目标项目：
```bash
cp scripts/extract_pdf.py <project>/
cp scripts/extract_pdf_mineru.py <project>/
cp scripts/extract_pdf_pymupdf.py <project>/
cp AGENTS_TEMPLATE.md <project>/AGENTS.md
```

2. 创建目录结构：
```bash
mkdir -p <project>/raw <project>/wiki/{sources,assets/figures,entities/{models,methods},concepts,synthesis}
```

3. 将 PDF 论文放入 `<project>/raw/`

4. 运行提取：
```bash
# 自动选择：如果配置了 TOKEN 则使用 MinerU，否则使用 pymupdf4llm
python extract_pdf.py

# 或显式指定后端：
python extract_pdf_mineru.py --all     # MinerU VLM（推荐）
python extract_pdf_pymupdf.py --all    # pymupdf4llm（备选）
```

## 后端选择逻辑

统一入口 `extract_pdf.py` 的选择逻辑：
- 检查 `MINERU_TOKEN` 环境变量或 `extract_pdf_mineru.py` 中的 `TOKEN` 变量
- 如果 Token 已配置且非空：使用 **MinerU VLM API**（质量更高）
- 如果 Token 为空或缺失：回退到 **pymupdf4llm**（本地运行，无需网络）

### MinerU VLM（推荐）

| 优势 | 说明 |
|------|------|
| 图片识别 | 基于 VLM，能区分论文插图与公式渲染图 |
| 公式提取 | 更高质量的 LaTeX 还原 |
| 表格解析 | 结构化识别，保留对齐格式 |
| 免费额度 | 每天 5000 篇文档，每篇最多 200 页 |

**配置方法**：访问 https://mineru.net/apiManage/docs ，注册后获取 API Key，设置环境变量：
```bash
export MINERU_TOKEN="your-api-key-here"
```

### PyMuPDF4LLM（备选）

本地运行，无需网络。安装依赖：
```bash
pip install pymupdf4llm pymupdf pillow
```

## 输出结构

```
wiki/sources/{paper_id}.md          # 论文 Markdown（含 frontmatter 元信息）
wiki/assets/figures/{paper_id}/
    fig1.png ... figN.png           # 提取的图片（全局编号）
    _index.json                     # 图片索引，含 figure_label 映射
```

## 核心创新：图片映射

`_index.json` 通过以下方式将提取图片映射到论文 Figure 编号：
1. 文本位置最近邻匹配（距离 < 5000 字符）
2. 基于面积的主图选择（同一 Figure 下最大图片优先）

LLM Agent 使用 `_index.json` 在实体页面中精确引用正确的图片。

## 命令

所有脚本支持以下参数：
```bash
python <script>.py              # 增量提取
python <script>.py --all        # 全量提取
python <script>.py --force PID  # 强制重新提取指定论文
python <script>.py --list       # 查看提取状态
```

## AGENTS.md 模板

使用 `AGENTS_TEMPLATE.md` 作为新项目的 Schema 层。可自定义：
- 研究主题和标签
- 实体分类（模型/方法/数据集）
- 页面格式规范

完整模板参见 [AGENTS_TEMPLATE.md](AGENTS_TEMPLATE.md)。

## 依赖

```bash
pip install pymupdf pymupdf4llm pillow requests
```
