#!/usr/bin/env python3
"""
提取 raw/ 文件夹中所有 PDF 的：
1. 文本内容 → wiki/sources/ .md (使用 pymupdf4llm 生成高质量 Markdown)
2. 图片 → wiki/assets/figures/{paper_id}/
   - 使用 PyMuPDF 提取内嵌图片（非整页）
   - 自适应放大，确保最小宽度 1200px
3. 图片索引 → wiki/assets/figures/{paper_id}/_index.json

支持增量提取：只处理新增或修改过的 PDF 文件
"""

import os
import sys
import json
import argparse
import fitz  # PyMuPDF
from pathlib import Path
from PIL import Image
from collections import defaultdict

# pymupdf4llm 导入
try:
    import pymupdf4llm
    PYMUPDF4LLM_AVAILABLE = True
except ImportError:
    PYMUPDF4LLM_AVAILABLE = False


# ---------------------------------------------------------------------------
# Venue 抽取
# ---------------------------------------------------------------------------
# 设计原则：
# 1. 只在文档首 / 尾部搜索（版权声明、页心 / 页脚、引用标题通常在这里），
#    避免正文及参考文献中对其他会议的偶然提及导致误判。
# 2. 优先级：强句式 (Proceedings of … <完整会议名>)
#                > 简写 + 年份 (SIGIR '24 / KDD 2025)
#                > 期刊模式 (TOIS / TACL / TKDE / JMLR)
#                > 单词边界的特异简写兑底 (SIGKDD / NeurIPS / RecSys ...)
#                > arXiv 预印本识别
# 3. NAACL 必须在 ACL 之前匹配，SIGKDD 必须在 KDD 之前，避免特殊限定符被常见简写吸走。
# ---------------------------------------------------------------------------

# 强句式："Proceedings of ..." + 会议全名 / 独特标志
# 使用 [^\n]{0,300} 允许中间有年份 / 地点 / 序数词等修饰。
_PROCEEDINGS_PATTERNS = [
    # NAACL 必须优先于 ACL（包含 Association for Computational Linguistics 子串）
    (r"Proceedings of[^\n]{0,300}North American Chapter of the Association for Computational Linguistics", "NAACL"),
    (r"Proceedings of[^\n]{0,300}Empirical Methods in Natural Language Processing", "EMNLP"),
    (r"Proceedings of[^\n]{0,300}International Conference on Computational Linguistics", "COLING"),
    (r"Proceedings of[^\n]{0,300}Annual Meeting of the Association for Computational Linguistics", "ACL"),
    (r"Findings of the Association for Computational Linguistics", "ACL"),
    # SIGKDD / KDD 优先于其他 "Conference on ..."
    (r"Proceedings of[^\n]{0,300}(?:ACM\s+)?SIGKDD", "KDD"),
    (r"Proceedings of[^\n]{0,300}Conference on Knowledge Discovery and Data Mining", "KDD"),
    # SIGIR
    (r"Proceedings of[^\n]{0,300}(?:ACM\s+)?SIGIR", "SIGIR"),
    (r"Proceedings of[^\n]{0,300}Conference on Research and Development in Information Retrieval", "SIGIR"),
    # CIKM
    (r"Proceedings of[^\n]{0,300}Conference on Information and Knowledge Management", "CIKM"),
    (r"Proceedings of[^\n]{0,300}\bCIKM\b", "CIKM"),
    # WWW / Web Conference
    (r"Proceedings of[^\n]{0,300}ACM Web Conference", "WWW"),
    (r"Proceedings of[^\n]{0,300}World Wide Web Conference", "WWW"),
    (r"Proceedings of[^\n]{0,300}\bTheWebConf\b", "WWW"),
    # RecSys
    (r"Proceedings of[^\n]{0,300}Conference on Recommender Systems", "RecSys"),
    (r"Proceedings of[^\n]{0,300}\bRecSys\b", "RecSys"),
    # WSDM
    (r"Proceedings of[^\n]{0,300}Conference on Web Search and Data Mining", "WSDM"),
    (r"Proceedings of[^\n]{0,300}\bWSDM\b", "WSDM"),
    # NeurIPS / NIPS
    (r"(?:Proceedings of[^\n]{0,300})?Conference on Neural Information Processing Systems", "NeurIPS"),
    (r"Advances in Neural Information Processing Systems", "NeurIPS"),
    # ICML / ICLR
    (r"Proceedings of[^\n]{0,300}International Conference on Machine Learning", "ICML"),
    (r"Proceedings of[^\n]{0,300}International Conference on Learning Representations", "ICLR"),
    # AAAI / IJCAI
    (r"Proceedings of[^\n]{0,300}AAAI Conference on Artificial Intelligence", "AAAI"),
    (r"Proceedings of[^\n]{0,300}International Joint Conference on Artificial Intelligence", "IJCAI"),
]

# 简写 + 年份："SIGIR '24" / "KDD 2025" / "WWW ’25"
_SHORT_WITH_YEAR_PATTERNS = [
    (r"\bSIGIR\s*['\u2018\u2019`]?\s*\d{2,4}\b", "SIGIR"),
    (r"\bCIKM\s*['\u2018\u2019`]?\s*\d{2,4}\b", "CIKM"),
    (r"\bKDD\s*['\u2018\u2019`]?\s*\d{2,4}\b", "KDD"),
    (r"\bWWW\s*['\u2018\u2019`]?\s*\d{2,4}\b", "WWW"),
    (r"\bRecSys\s*['\u2018\u2019`]?\s*\d{2,4}\b", "RecSys"),
    (r"\bWSDM\s*['\u2018\u2019`]?\s*\d{2,4}\b", "WSDM"),
    (r"\bNeurIPS\s*\d{2,4}\b", "NeurIPS"),
    (r"\bNIPS\s*\d{2,4}\b", "NeurIPS"),
    (r"\bICML\s*\d{2,4}\b", "ICML"),
    (r"\bICLR\s*\d{2,4}\b", "ICLR"),
    (r"\bACL\s*\d{2,4}\b", "ACL"),
    (r"\bEMNLP\s*\d{2,4}\b", "EMNLP"),
    (r"\bNAACL[- ]?HLT?\s*\d{2,4}\b", "NAACL"),
    (r"\bNAACL\s*\d{2,4}\b", "NAACL"),
    (r"\bCOLING\s*\d{2,4}\b", "COLING"),
    (r"\bAAAI[- ]?\d{2,4}\b", "AAAI"),
    (r"\bIJCAI[- ]?\d{2,4}\b", "IJCAI"),
]

# 期刊（完整名或严格词边界的缩写）
_JOURNAL_PATTERNS = [
    (r"ACM Transactions on Information Systems|\bTOIS\b", "TOIS"),
    (r"Transactions of the Association for Computational Linguistics|\bTACL\b", "TACL"),
    (r"IEEE Transactions on Knowledge and Data Engineering|\bTKDE\b", "TKDE"),
    (r"Journal of Machine Learning Research|\bJMLR\b", "JMLR"),
    (r"Journal of the American Society for Information Science and Technology|\bJASIST\b", "JASIST"),
]

# 特异简写兑底（不依赖年份、误触发概率极低）
_UNIQUE_ACRONYM_PATTERNS = [
    (r"\bSIGKDD\b", "KDD"),
    (r"\bNeurIPS\b", "NeurIPS"),
    (r"\bRecSys\b", "RecSys"),
    (r"\bWSDM\b", "WSDM"),
    (r"\bICLR\b", "ICLR"),
    (r"\bICML\b", "ICML"),
    (r"\bEMNLP\b", "EMNLP"),
    (r"\bCOLING\b", "COLING"),
    (r"\bNAACL\b", "NAACL"),
]


# 文件名 → venue 映射（ACL Anthology / ACM DOI 命名约定）
# 格式例：
#   2025.coling-industry.12     → COLING
#   2024.emnlp-main.456         → EMNLP
#   2024.naacl-long.7           → NAACL
#   2023.findings-acl.123       → ACL
_FILENAME_VENUE_TOKENS = {
    "coling": "COLING",
    "emnlp": "EMNLP",
    "naacl": "NAACL",
    "acl": "ACL",
    "eacl": "ACL",
    "findings": "ACL",
}


def _detect_venue_from_stem(pdf_stem: str) -> str:
    """从 PDF 文件名识别 ACL Anthology 风格的 venue。无法识别时返回空字符串。"""
    if not pdf_stem:
        return ""
    import re as _re
    # ACL Anthology 格式：{year}.{venue}[-{track}].{id}
    m = _re.match(r"^(\d{4})\.([a-z]+)(?:-[a-z]+)?\.\d+$", pdf_stem.lower())
    if m:
        token = m.group(2)
        if token in _FILENAME_VENUE_TOKENS:
            return _FILENAME_VENUE_TOKENS[token]
    return ""


def _prepare_head(full_text: str) -> str:
    """取文档首 8000 字符，并在 References/Bibliography 标题处截断，避免参考文献污染。

    说明：从工程经验看，论文的会议 / 期刊 信息几乎总出现在首页（标题周围、版权页脚
    或 abstract 之前/之后的 venue 标记），占不超 8000 字符。文档尾部则大概率是参考文献，
    易被其他会议名污染，故不再搜索尾部。
    """
    import re as _re
    head = full_text[:8000]
    ref_match = _re.search(
        r"\n#+\s*(?:References|Bibliography|REFERENCES|BIBLIOGRAPHY)\b",
        head,
    )
    if ref_match:
        head = head[:ref_match.start()]
    return head


def detect_venue(full_text: str, pdf_stem: str = "") -> str:
    """从 Markdown 文本和文件名中抽取发表 venue。

    优先级：
      1. 文件名约定 (ACL Anthology: 2025.coling-industry.xx)
      2. 强句式 (Proceedings of … <完整会议名>)
      3. 简写 + 年份 (SIGIR '24 / KDD 2025)
      4. 期刊 (TOIS / TACL / TKDE / JMLR)
      5. 特异简写 (SIGKDD / NeurIPS / RecSys …)
      6. arXiv 预印本兑底

    未命中时返回空字符串。
    """
    import re as _re

    # 1) 文件名强信号
    name = _detect_venue_from_stem(pdf_stem)
    if name:
        return name

    head = _prepare_head(full_text)

    # 2)-5) 按优先级逐层在 head 中匹配
    for pattern_list, flags in (
        (_PROCEEDINGS_PATTERNS, _re.IGNORECASE),
        (_SHORT_WITH_YEAR_PATTERNS, 0),
        (_JOURNAL_PATTERNS, 0),
        (_UNIQUE_ACRONYM_PATTERNS, 0),
    ):
        for pattern, name in pattern_list:
            if _re.search(pattern, head, flags):
                return name

    # 6) arXiv 预印本兑底（head 内有 arXiv:YYMM.NNNNN 或 arxiv.org）
    if _re.search(r"\barXiv:\s*\d{4}\.\d{4,5}", head, _re.IGNORECASE) or \
       _re.search(r"arxiv\.org/abs/", head, _re.IGNORECASE):
        return "arXiv"

    return ""


def extract_pdf_to_markdown(pdf_path, md_path, figure_dir):
    """使用 pymupdf4llm 提取单个 PDF 为 Markdown"""
    try:
        # 使用 PyMuPDF 获取 PDF 元数据
        doc = fitz.open(str(pdf_path))
        pdf_metadata = doc.metadata
        doc.close()
        
        # 确保图片目录存在
        figure_dir.mkdir(parents=True, exist_ok=True)
        paper_id = figure_dir.name
        
        # 使用 pymupdf4llm 转换 PDF
        # 先提取图片到临时目录
        temp_img_dir = figure_dir / "_temp"
        temp_img_dir.mkdir(parents=True, exist_ok=True)
        
        full_text = pymupdf4llm.to_markdown(
            str(pdf_path),
            dpi=400,
            embed_images=False,
            write_images=True,
            image_path=str(temp_img_dir),
            image_format="png",
            header=True,
            footer=False,
            page_separators=False,
        )
        
        # 移动并重命名图片到标准目录，同时建立原始文件名到新文件名的映射
        saved_images = []
        img_name_map = {}  # 原始文件名 -> 新文件名
        fig_metadata = []   # 用于 _index.json 的图片元数据
        if temp_img_dir.exists():
            # pymupdf4llm 文件名格式: {pdf_name}-{page_idx}-{seq}.png
            import re
            all_images = []
            for img_file in sorted(temp_img_dir.iterdir()):
                if img_file.suffix.lower() in ['.png', '.jpg', '.jpeg']:
                    # 解析文件名提取页码
                    match = re.search(r'-0*(\d+)-\d+\.', img_file.name)
                    page_num = int(match.group(1)) if match else 0
                    all_images.append((page_num, img_file))
            
            # 按页码+文件名排序，确保全局顺序一致
            all_images.sort(key=lambda x: (x[0], x[1].name))
            
            # 全局顺序命名: fig1.png, fig2.png...
            for fig_idx, (page_num, img_file) in enumerate(all_images, 1):
                new_name = f"fig{fig_idx}.png"
                new_path = figure_dir / new_name
                # 避免冲突
                while new_path.exists():
                    fig_idx += 1
                    new_name = f"fig{fig_idx}.png"
                    new_path = figure_dir / new_name
                
                # 记录映射关系
                img_name_map[img_file.name] = new_name
                img_file.rename(new_path)
                saved_images.append(new_name)
                
                # 获取图片尺寸用于索引
                try:
                    with Image.open(new_path) as im:
                        width, height = im.size
                except:
                    width, height = 0, 0
                
                fig_metadata.append({
                    "page": page_num,
                    "figure_index": fig_idx,
                    "filename": new_name,
                    "path": f"assets/figures/{paper_id}/{new_name}",
                    "width": width,
                    "height": height,
                    "size_kb": round(new_path.stat().st_size / 1024, 2),
                    "type": "figure"
                })
            
            # 删除临时目录
            import shutil
            shutil.rmtree(temp_img_dir)
        
        # 更新 markdown 中的图片路径
        # pymupdf4llm 在 markdown 中引用的图片路径可能包含目录前缀
        # 例如: ![alt](_temp/2505.18654v4.pdf-0003-02.png) 或 ![alt](2505.18654v4.pdf-0003-02.png)
        import re
        for orig_name, new_name in img_name_map.items():
            # 转义原始文件名中的特殊字符用于正则匹配
            escaped_name = re.escape(orig_name)
            # 匹配各种路径格式的图片引用
            # 格式1: ![...](orig_name)
            # 格式2: ![...](_temp/orig_name)
            # 格式3: ![...](path/to/_temp/orig_name)
            pattern = r'!\[([^\]]*)\]\([^)]*' + escaped_name + r'\)'
            if re.search(pattern, full_text):
                full_text = re.sub(
                    pattern,
                    lambda m: f'![{m.group(1)}](assets/figures/{paper_id}/{new_name})',
                    full_text
                )
        
        # 推断每个图片对应的 Figure 编号
        # 策略：在 markdown 文本中搜索 "Figure X" 位置，与图片位置做最近邻匹配
        if fig_metadata:
            figure_refs = []
            for m in re.finditer(r'Figure\s+(\d+)[\s|:]', full_text):
                figure_refs.append({
                    'number': int(m.group(1)),
                    'pos': m.start(),
                    'label': f"Figure {m.group(1)}"
                })
            
            if figure_refs:
                img_refs = []
                pattern = r'!\[.*?\]\(assets/figures/' + re.escape(paper_id) + r'/fig(\d+)\.png\)'
                for m in re.finditer(pattern, full_text):
                    img_refs.append({
                        'fig_idx': int(m.group(1)),
                        'pos': m.start()
                    })
                
                # 最近邻匹配：每个图片找距离最近的 Figure（阈值 5000 字符）
                for img in img_refs:
                    nearest = None
                    min_dist = float('inf')
                    for fig in figure_refs:
                        dist = abs(img['pos'] - fig['pos'])
                        if dist < min_dist:
                            min_dist = dist
                            nearest = fig
                    if nearest and min_dist < 5000:
                        # 写入对应的 fig_metadata 项
                        for meta in fig_metadata:
                            if meta['figure_index'] == img['fig_idx']:
                                meta['figure_label'] = nearest['label']
                                break
                
                # 主图选择：同一个 Figure 可能对应多张图片（大图+小公式）
                # 按面积排序，只保留每个 Figure 中面积最大的一张作为主图
                figure_groups = defaultdict(list)
                for meta in fig_metadata:
                    if 'figure_label' in meta:
                        figure_groups[meta['figure_label']].append(meta)
                
                for figure_label, images in figure_groups.items():
                    if len(images) > 1:
                        # 按面积（width * height）降序排序
                        images.sort(key=lambda x: x['width'] * x['height'], reverse=True)
                        # 只保留最大的那张的 figure_label，其余清除
                        for img in images[1:]:
                            del img['figure_label']
        
        # 将 LaTeX 定界符转换为 Obsidian 兼容格式
        # \[...\] → $$...$$ (块级公式)
        full_text = re.sub(r'\\\[', '$$', full_text)
        full_text = re.sub(r'\\\]', '$$', full_text)
        # \(...\) → $...$ (行内公式)
        full_text = re.sub(r'\\\(', '$', full_text)
        full_text = re.sub(r'\\\)', '$', full_text)

        # 构建完整的 frontmatter
        metadata_lines = ["---"]
        
        # title: 从 PDF 元数据或内容中获取
        title = ""
        if pdf_metadata and pdf_metadata.get("title"):
            title = pdf_metadata["title"]
        # 从 markdown 第一行提取标题作为备选
        if not title and full_text.strip():
            first_lines = full_text.strip().split('\n')[:10]
            for line in first_lines:
                line = line.strip()
                if line.startswith('# ') and len(line) > 2:
                    title = line.lstrip('#').strip()
                    break
        if title:
            metadata_lines.append(f'title: "{title}"')
        
        # date: 从 PDF 文件名或元数据推断
        date = ""
        if pdf_metadata and pdf_metadata.get("creationDate"):
            cd = pdf_metadata["creationDate"]
            if cd.startswith("D:") and len(cd) >= 10:
                date = cd[2:6] + "-" + cd[6:8]
        if not date:
            fname = pdf_path.stem
            if len(fname) >= 4 and fname[:2] in ['18', '19', '20', '21', '22', '23', '24', '25', '26']:
                year = fname[:2]
                month = fname[2:4] if len(fname) >= 4 else "01"
                date = f"20{year}-{month}"
        if date:
            metadata_lines.append(f'date: {date}')
        
        # arxiv: 从文件名提取
        arxiv_id = pdf_path.stem
        metadata_lines.append(f'arxiv: {arxiv_id}')
        
        # authors: 从 PDF 元数据获取
        authors = []
        if pdf_metadata and pdf_metadata.get("author"):
            author_str = pdf_metadata["author"]
            if ";" in author_str:
                authors = [a.strip() for a in author_str.split(";")]
            elif " and " in author_str:
                authors = [a.strip() for a in author_str.split(" and ")]
            else:
                authors = [author_str.strip()]
        if authors:
            metadata_lines.append(f'authors: {json.dumps(authors, ensure_ascii=False)}')
        
        # venue: 尝试从 markdown 中提取会议名（优先强句式，避免关键词首命中导致误判）
        venue = detect_venue(full_text, pdf_stem=pdf_path.stem)
        if venue:
            metadata_lines.append(f'venue: "{venue}"')
        
        # tags: 添加论文标签
        tags = ["paper"]
        title_lower = title.lower() if title else ""
        text_lower = full_text.lower()
        if "recommend" in title_lower or "recommend" in text_lower[:2000]:
            tags.append("recommendation")
        if "generative" in title_lower or "generative" in text_lower[:2000]:
            tags.append("generative-recommendation")
        if "scaling" in title_lower or "scaling law" in text_lower[:2000]:
            tags.append("scaling-law")
        if "sequential" in title_lower or "sequential" in text_lower[:2000]:
            tags.append("sequential-recommendation")
        if "ctr" in text_lower[:2000] or "click-through" in text_lower[:2000]:
            tags.append("ctr-prediction")
        if "search" in title_lower or "search" in text_lower[:2000]:
            tags.append("search")
        if "transformer" in title_lower or "transformer" in text_lower[:2000]:
            tags.append("transformer")
        if "attention" in title_lower or "attention" in text_lower[:2000]:
            tags.append("attention")
        metadata_lines.append(f'tags: {json.dumps(tags, ensure_ascii=False)}')
        
        metadata_lines.append(f'source_pdf: "{pdf_path.name}"')
        metadata_lines.append("---")
        metadata_lines.append("")
        
        # 组合最终内容
        final_content = "\n".join(metadata_lines) + full_text
        
        # 写入 Markdown 文件
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(final_content)
        
        return True, {
            "chars": len(final_content),
            "images": saved_images,
            "fig_metadata": fig_metadata,
            "metadata": {
                "title": title,
                "date": date,
                "arxiv": arxiv_id,
                "authors": authors,
                "venue": venue,
                "tags": tags
            }
        }
        
    except Exception as e:
        return False, str(e)


def needs_extraction(pdf_file, md_path, figure_dir):
    """
    检查是否需要重新提取 PDF 内容
    返回 True 如果需要提取（文件新增或已修改）
    """
    # 如果 Markdown 文件不存在，需要提取
    if not md_path.exists():
        return True, "新增文件"
    
    # 如果图片目录不存在，需要提取
    if not figure_dir.exists():
        return True, "图片目录缺失"
    
    # 如果索引文件不存在，需要提取
    index_path = figure_dir / "_index.json"
    if not index_path.exists():
        return True, "索引文件缺失"
    
    # 比较修改时间
    pdf_mtime = pdf_file.stat().st_mtime
    md_mtime = md_path.stat().st_mtime
    
    # 如果 PDF 比 Markdown 文件新，需要重新提取
    if pdf_mtime > md_mtime:
        return True, "PDF 已更新"
    
    return False, "已是最新"


def extract_single_pdf(pdf_file, sources_dir, assets_dir, force=False):
    """提取单个 PDF 的完整内容（Markdown + 图片）"""
    paper_id = pdf_file.stem
    md_path = sources_dir / f"{paper_id}.md"
    figure_dir = assets_dir / paper_id
    
    # 强制模式下清理旧提取的图片
    if force and figure_dir.exists():
        for old_img in figure_dir.iterdir():
            if old_img.name.startswith("fig") and old_img.suffix == ".png":
                old_img.unlink()
    
    # 提取 Markdown（包含 pymupdf4llm 提取的图片）
    success, result = extract_pdf_to_markdown(pdf_file, md_path, figure_dir)
    
    if not success:
        return False, f"Markdown 提取失败: {result}"
    
    fig_metadata = result.get("fig_metadata", [])
    
    # 写入 _index.json
    if fig_metadata:
        try:
            doc = fitz.open(pdf_file)
            metadata = doc.metadata
            doc.close()
        except:
            metadata = {}
        
        index_data = {
            "paper_id": paper_id,
            "total_figures": len(fig_metadata),
            "figures": fig_metadata
        }
        if metadata:
            index_data["title"] = metadata.get("Title", "")
            index_data["author"] = metadata.get("Author", "")
        
        index_path = figure_dir / "_index.json"
        with open(index_path, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, indent=2, ensure_ascii=False)
    
    return True, {
        "chars": result["chars"],
        "figures": fig_metadata,
        "metadata": result.get("metadata", {})
    }


def main():
    # 检查 pymupdf4llm 是否可用
    if not PYMUPDF4LLM_AVAILABLE:
        print("✗ 错误: pymupdf4llm 未安装")
        print("   请运行: pip install pymupdf4llm")
        sys.exit(1)
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description='提取 PDF 为 Markdown 和图片到 wiki 文件夹',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python extract_pdf_pymupdf.py              # 增量提取（只处理新增/修改的文件）
  python extract_pdf_pymupdf.py --all        # 全量提取（处理所有文件）
  python extract_pdf_pymupdf.py --force 2505.18654v4  # 强制提取指定文件
  python extract_pdf_pymupdf.py --list       # 列出所有 PDF 及其提取状态
        """
    )
    parser.add_argument(
        '--all', '-a',
        action='store_true',
        help='全量提取：处理所有 PDF 文件（忽略缓存）'
    )
    parser.add_argument(
        '--force', '-f',
        metavar='PAPER_ID',
        help='强制提取指定 paper_id 的文件（如：2505.18654v4）'
    )
    parser.add_argument(
        '--list', '-l',
        action='store_true',
        help='列出所有 PDF 及其提取状态'
    )
    
    args = parser.parse_args()
    
    base_dir = Path(__file__).parent.resolve()
    raw_dir = base_dir / "raw"
    sources_dir = base_dir / "wiki" / "sources"
    assets_dir = base_dir / "wiki" / "assets" / "figures"
    
    # 确保输出目录存在
    sources_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)
    
    # 获取所有 PDF 文件
    pdf_files = sorted([f for f in raw_dir.iterdir() if f.suffix.lower() == '.pdf'])
    
    # 如果只列出状态
    if args.list:
        print(f"{'状态':<8} {'文件名':<40} {'说明'}")
        print("-" * 80)
        for pdf_file in pdf_files:
            paper_id = pdf_file.stem
            md_path = sources_dir / f"{paper_id}.md"
            figure_dir = assets_dir / paper_id
            
            needs, reason = needs_extraction(pdf_file, md_path, figure_dir)
            status = "待提取" if needs else "已完成"
            print(f"{status:<8} {pdf_file.name:<40} {reason}")
        return
    
    # 确定要处理的文件
    files_to_process = []
    skipped_files = []
    
    for pdf_file in pdf_files:
        paper_id = pdf_file.stem
        md_path = sources_dir / f"{paper_id}.md"
        figure_dir = assets_dir / paper_id
        
        # 强制模式：只处理指定文件
        if args.force:
            if paper_id == args.force:
                files_to_process.append(pdf_file)
            continue
        
        # 全量模式：处理所有文件
        if args.all:
            files_to_process.append(pdf_file)
            continue
        
        # 增量模式：只处理需要提取的文件
        needs, reason = needs_extraction(pdf_file, md_path, figure_dir)
        if needs:
            files_to_process.append(pdf_file)
        else:
            skipped_files.append((pdf_file, reason))
    
    # 强制模式下，如果指定文件不存在，报错
    if args.force and not files_to_process:
        print(f"✗ 错误: 找不到指定的 PDF 文件 '{args.force}'")
        print(f"   可用文件: {', '.join([f.stem for f in pdf_files])}")
        sys.exit(1)
    
    # 显示处理计划
    mode_str = "全量提取" if args.all else (f"强制提取: {args.force}" if args.force else "增量提取")
    print(f"模式: {mode_str}")
    print(f"PDF 总数: {len(pdf_files)}")
    
    if not args.all and not args.force:
        print(f"待处理: {len(files_to_process)} 个")
        print(f"跳过: {len(skipped_files)} 个（已是最新）")
    
    print("-" * 60)
    
    # 如果没有需要处理的文件
    if not files_to_process:
        print("✓ 所有文件都已是最新，无需提取")
        return
    
    # 处理文件
    success_count = 0
    total_figures = 0
    
    for pdf_file in files_to_process:
        paper_id = pdf_file.stem
        
        print(f"处理: {pdf_file.name} ... ", end="", flush=True)
        
        success, result = extract_single_pdf(pdf_file, sources_dir, assets_dir, force=bool(args.force))
        
        if success:
            fig_count = len(result["figures"])
            total_figures += fig_count
            print(f"✓ (文本: {result['chars']} 字符, 图片: {fig_count} 张)")
            success_count += 1
        else:
            print(f"✗ 错误: {result}")
    
    print("-" * 60)
    print(f"完成: {success_count}/{len(files_to_process)} 个文件已提取")
    
    if skipped_files and not args.all:
        print(f"跳过: {len(skipped_files)} 个文件（已是最新）")
    
    print(f"Markdown 目录: {sources_dir}")
    print(f"图片目录: {assets_dir}")
    print(f"总计提取图片: {total_figures} 张")


if __name__ == "__main__":
    main()
