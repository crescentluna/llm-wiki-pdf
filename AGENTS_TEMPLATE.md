# LLM-Wiki Schema for [YOUR RESEARCH TOPIC]

> 基于 Karpathy 的 LLM-Wiki 设计模式
> https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f#file-llm-wiki-md
> 项目路径: `[YOUR PROJECT PATH]`

---

## 架构概览

```
raw/          → 原始文献（PDF论文）- 只读，不可修改
wiki/         → LLM 生成的知识库 - LLM 全权维护
AGENTS.md     → 本文件 - 定义结构、规范和工作流
extract_pdf.py → PDF 提取统一入口（自动选择 MinerU/pymupdf4llm）
extract_pdf_mineru.py → MinerU VLM 提取脚本（推荐）
extract_pdf_pymupdf.py → pymupdf4llm 提取脚本（备选）
```

---

## Wiki 目录结构

```
wiki/
├── .obsidian/         # Obsidian 配置
├── assets/
│   └── figures/       # 论文提取图片
│       └── {paper_id}/
│           ├── fig1.png
│           ├── fig2.png
│           └── _index.json
├── index.md           # 内容索引：所有页面的目录
├── log.md             # 操作日志：按时间顺序记录所有操作
├── overview.md        # 知识库总览：研究主题、核心观点、进展
├── sources/           # 文献摘要：每篇论文的详细摘要
│   ├── {arxiv_id}.md
│   └── ...
├── entities/          # 实体页面：模型、方法、数据集、作者、机构
│   ├── models/        # 模型架构
│   ├── methods/       # 技术方法
│   ├── datasets/      # 数据集
│   └── people/        # 研究人员、机构
├── concepts/          # 概念页面：核心概念、理论、现象
│   ├── training/      # 训练相关概念
│   ├── evaluation/    # 评估相关概念
│   └── capabilities/  # 能力相关概念
└── synthesis/         # 综合页面：对比、综述、趋势分析
    ├── comparisons/   # 模型/方法对比
    └── trends/        # 研究趋势分析
```

---

## 页面规范

### 1. 文献摘要页 (`sources/*.md`)

每篇论文一个页面，文件名格式：`{arxiv_id}.md`

```markdown
---
title: 论文标题
date: YYYY-MM-DD
arxiv: arxiv_id
authors: [作者列表]
venue: 发表会议/期刊
tags: [相关标签]
---

# 论文标题

## 核心贡献
- 要点1
- 要点2

## 方法概述
...

## 关键结果
...

## 与其他工作的关系
- 基于: [[相关论文]]
- 改进: [[相关方法]]

## 个人笔记
...
```

### 2. 实体页面 (`entities/**/*.md`)

```markdown
---
type: model|method|dataset|person|institution
date: YYYY-MM-DD
sources: [来源论文列表]
---

# 实体名称

## 定义
...

## 核心特性
...

## 变体/演进
...

## 相关实体
- [[相关实体1]]
- [[相关实体2]]

## 来源文献
- [[arxiv_id]]
```

### 3. 概念页面 (`concepts/**/*.md`)

```markdown
---
type: concept
date: YYYY-MM-DD
sources: [来源论文列表]
---

# 概念名称

## 定义
...

## 重要性
...

## 相关概念
- [[相关概念1]]
- [[相关概念2]]

## 来源文献
- [[arxiv_id]]
```

---

## 操作工作流

### 1. Ingest（文献录入）

当有新论文加入 `raw/` 时：

1. **提取论文**：运行 `python extract_pdf.py`（自动选择 MinerU 或 pymupdf4llm）
2. **阅读摘要**：阅读 `sources/{paper_id}.md` 提取核心信息
3. **提取实体**：识别论文中的模型、方法、数据集、关键人物
4. **更新实体页**：
   - 如果实体已存在，追加新信息
   - 如果实体不存在，创建新页面（记得引用提取的图片）
5. **更新概念页**：识别核心概念，更新或创建概念页面
6. **更新索引**：在 `index.md` 中添加新页面条目
7. **记录日志**：在 `log.md` 中追加录入记录
8. **更新总览**：如有必要，更新 `overview.md`

**日志格式**：
```markdown
## [YYYY-MM-DD] ingest | 论文标题
- 文件: raw/arxiv_id.pdf
- 创建: wiki/sources/arxiv_id.md
- 更新实体: [[实体1]], [[实体2]]
- 更新概念: [[概念1]]
```

### 2. Query（查询回答）

1. **查阅索引**：先读 `index.md` 找到相关页面
2. **深入阅读**：读取相关页面
3. **综合回答**：基于 wiki 内容回答，带引用链接
4. **可选归档**：如果回答有价值，创建 `synthesis/` 页面保存

### 3. Lint（健康检查）

定期执行：

1. **检查冲突**：识别矛盾陈述
2. **发现孤儿页**：找出没有入链的页面
3. **识别缺口**：发现被提及但未创建的重要概念
4. **更新交叉引用**：添加缺失的链接

---

## PDF 图片提取

### 提取方案

本项目提供双提取方案：

#### 方案 A（推荐）：MinerU VLM

使用 `extract_pdf_mineru.py`，需要配置 `MINERU_TOKEN` 环境变量：

```bash
export MINERU_TOKEN="your-api-key"  # 从 https://mineru.net/apiManage/docs 申请
python extract_pdf_mineru.py              # 增量提取
python extract_pdf_mineru.py --all        # 全量提取
```

**特点**：图片识别更准确，公式提取质量更高，需要网络连接。

#### 方案 B（备选）：PyMuPDF4LLM

使用 `extract_pdf_pymupdf.py`，纯本地运行：

```bash
pip install pymupdf4llm pymupdf pillow
python extract_pdf_pymupdf.py              # 增量提取
python extract_pdf_pymupdf.py --all        # 全量提取
```

**特点**：无需网络，适合离线环境。

#### 统一入口

```bash
python extract_pdf.py    # 自动选择：有 TOKEN 用 MinerU，否则用 pymupdf4llm
```

### 输出结构

```
wiki/sources/{paper_id}.md          # 论文 Markdown（含 frontmatter）
wiki/assets/figures/{paper_id}/
├── fig1.png                         # 论文图片（全局顺序命名）
├── fig2.png
├── ...
└── _index.json                      # 图片索引（含 figure_label）
```

### 在文档中引用图片

```markdown
---
type: model
figures:
  - page: 3
    file: "../../assets/figures/{paper_id}/fig1.png"
    description: "Model Architecture"
---

# Model Name

## 架构

![Model Architecture](../../assets/figures/{paper_id}/fig1.png)
*模型架构（来源：论文 Figure 1）*
```

### 查找图片对应的论文页码

1. 查看 `wiki/assets/figures/{paper_id}/_index.json`
2. 用图片查看器打开图片，确认内容后引用

---

## 研究主题

本 wiki 聚焦于 **[YOUR RESEARCH TOPIC]** 研究，包括：

- **[方向1]**：描述
- **[方向2]**：描述
- **[方向3]**：描述

---

## 命名约定

- **文件名**：使用 arxiv_id 或 kebab-case 英文
- **链接格式**：使用 Obsidian 风格 `[[页面名]]`
- **标签**：使用小写
- **日期格式**：YYYY-MM-DD
