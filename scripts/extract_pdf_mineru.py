#!/usr/bin/env python3
"""
提取 raw/ 文件夹中所有 PDF 的：
  1. 文本内容 → wiki/sources/{paper_id}.md        (使用 MinerU VLM 生成高质量 Markdown)
  2. 图片     → wiki/assets/figures/{paper_id}/fig{N}.png
  3. 图片索引 → wiki/assets/figures/{paper_id}/_index.json

相较 extract_pdf_pymupdf.py (pymupdf4llm 版本) 的升级：
  * Markdown 质量：MinerU VLM 对公式 / 表格 / 复杂版面识别更准
  * 图片 figure_label：直接从 content_list.json 每个 image block 的 image_caption
    解析 "Figure N"，不再依赖正文「最近邻匹配」启发式
  * venue 识别：以 content_list 中 page_idx==0 的文本作为首页头部，避免参考文献污染

支持：
    python extract_pdf_mineru.py               # 增量提取
    python extract_pdf_mineru.py --all         # 全量提取
    python extract_pdf_mineru.py --force PID   # 强制重抽指定论文
    python extract_pdf_mineru.py --list        # 列出待提取状态
    python extract_pdf_mineru.py --keep-raw    # 保留 MinerU 解压产物到 mineru_test/
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import fitz  # PyMuPDF
import requests
from PIL import Image


# =========================================================================
# 配置
# =========================================================================
# MinerU API Token - 从环境变量 MINERU_TOKEN 获取，或直接填写在此处
# 申请地址: https://mineru.net/apiManage/docs (免费，单日上限5000份文档，每份不超过200页)
TOKEN = os.environ.get("MINERU_TOKEN", "").strip()

API_BASE = "https://mineru.net/api/v4"
BATCH_URL = f"{API_BASE}/file-urls/batch"
RESULT_URL_TMPL = f"{API_BASE}/extract-results/batch/{{batch_id}}"

LANGUAGE = "en"
DEFAULT_MODEL_VERSION = "vlm"

POLL_INTERVAL = 10          # 秒
POLL_TIMEOUT = 60 * 30      # 单批次最多 30 分钟

HEADERS_JSON = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {TOKEN}",
}


# =========================================================================
# Venue 抽取（与 extract_pdf_pymupdf.py 同，不再依赖全文，而是使用 MinerU 的首页文本）
# =========================================================================
_PROCEEDINGS_PATTERNS = [
    (r"Proceedings of[^\n]{0,300}North American Chapter of the Association for Computational Linguistics", "NAACL"),
    (r"Proceedings of[^\n]{0,300}Empirical Methods in Natural Language Processing", "EMNLP"),
    (r"Proceedings of[^\n]{0,300}International Conference on Computational Linguistics", "COLING"),
    (r"Proceedings of[^\n]{0,300}Annual Meeting of the Association for Computational Linguistics", "ACL"),
    (r"Findings of the Association for Computational Linguistics", "ACL"),
    (r"Proceedings of[^\n]{0,300}(?:ACM\s+)?SIGKDD", "KDD"),
    (r"Proceedings of[^\n]{0,300}Conference on Knowledge Discovery and Data Mining", "KDD"),
    (r"Proceedings of[^\n]{0,300}(?:ACM\s+)?SIGIR", "SIGIR"),
    (r"Proceedings of[^\n]{0,300}Conference on Research and Development in Information Retrieval", "SIGIR"),
    (r"Proceedings of[^\n]{0,300}Conference on Information and Knowledge Management", "CIKM"),
    (r"Proceedings of[^\n]{0,300}\bCIKM\b", "CIKM"),
    (r"Proceedings of[^\n]{0,300}ACM Web Conference", "WWW"),
    (r"Proceedings of[^\n]{0,300}World Wide Web Conference", "WWW"),
    (r"Proceedings of[^\n]{0,300}\bTheWebConf\b", "WWW"),
    (r"Proceedings of[^\n]{0,300}Conference on Recommender Systems", "RecSys"),
    (r"Proceedings of[^\n]{0,300}\bRecSys\b", "RecSys"),
    (r"Proceedings of[^\n]{0,300}Conference on Web Search and Data Mining", "WSDM"),
    (r"Proceedings of[^\n]{0,300}\bWSDM\b", "WSDM"),
    (r"(?:Proceedings of[^\n]{0,300})?Conference on Neural Information Processing Systems", "NeurIPS"),
    (r"Advances in Neural Information Processing Systems", "NeurIPS"),
    (r"Proceedings of[^\n]{0,300}International Conference on Machine Learning", "ICML"),
    (r"Proceedings of[^\n]{0,300}International Conference on Learning Representations", "ICLR"),
    (r"Proceedings of[^\n]{0,300}AAAI Conference on Artificial Intelligence", "AAAI"),
    (r"Proceedings of[^\n]{0,300}International Joint Conference on Artificial Intelligence", "IJCAI"),
]

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

_JOURNAL_PATTERNS = [
    (r"ACM Transactions on Information Systems|\bTOIS\b", "TOIS"),
    (r"Transactions of the Association for Computational Linguistics|\bTACL\b", "TACL"),
    (r"IEEE Transactions on Knowledge and Data Engineering|\bTKDE\b", "TKDE"),
    (r"Journal of Machine Learning Research|\bJMLR\b", "JMLR"),
    (r"Journal of the American Society for Information Science and Technology|\bJASIST\b", "JASIST"),
]

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

# ACL Anthology / ACM DOI 文件名 → venue
_FILENAME_VENUE_TOKENS = {
    "coling": "COLING",
    "emnlp": "EMNLP",
    "naacl": "NAACL",
    "acl": "ACL",
    "eacl": "ACL",
    "findings": "ACL",
}


def _detect_venue_from_stem(pdf_stem: str) -> str:
    """从 PDF 文件名识别 venue：优先 ACL Anthology，其次 arXiv id。"""
    if not pdf_stem:
        return ""
    stem = pdf_stem.lower()
    # ACL Anthology：{year}.{venue}[-{track}].{id}
    m = re.match(r"^(\d{4})\.([a-z]+)(?:-[a-z]+)?\.\d+$", stem)
    if m:
        token = m.group(2)
        if token in _FILENAME_VENUE_TOKENS:
            return _FILENAME_VENUE_TOKENS[token]
    # arXiv id：{YYMM}.{NNNNN}[vN]
    if re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", stem):
        return "arXiv"
    return ""


def detect_venue(head_text: str, pdf_stem: str = "") -> str:
    """从首页文本和文件名中抽取发表 venue。

    优先级：文件名约定 → 强句式 → 简写+年份 → 期刊 → 特异简写 → arXiv 兜底。
    head_text 期望传入 MinerU content_list 中 page_idx==0 的拼接文本
    （避免正文/参考文献污染）。
    """
    name = _detect_venue_from_stem(pdf_stem)
    if name:
        return name

    head = head_text[:8000]
    # 若出现 References 标题提前截断，防御性处理
    ref_match = re.search(
        r"\n#+\s*(?:References|Bibliography|REFERENCES|BIBLIOGRAPHY)\b",
        head,
    )
    if ref_match:
        head = head[: ref_match.start()]

    for pattern_list, flags in (
        (_PROCEEDINGS_PATTERNS, re.IGNORECASE),
        (_SHORT_WITH_YEAR_PATTERNS, 0),
        (_JOURNAL_PATTERNS, 0),
        (_UNIQUE_ACRONYM_PATTERNS, 0),
    ):
        for pattern, name in pattern_list:
            if re.search(pattern, head, flags):
                return name

    if re.search(r"\barXiv:\s*\d{4}\.\d{4,5}", head, re.IGNORECASE) or \
       re.search(r"arxiv\.org/abs/", head, re.IGNORECASE):
        return "arXiv"

    return ""


# =========================================================================
# MinerU API
# =========================================================================
def log(msg: str) -> None:
    print(f"[mineru] {msg}", flush=True)


def request_upload_urls(pdfs: list[Path], model_version: str) -> tuple[str, list[str]]:
    """批量申请上传 URL，返回 batch_id 与 URL 列表。"""
    files_payload = [
        {"name": pdf.name, "data_id": pdf.stem, "language": LANGUAGE}
        for pdf in pdfs
    ]
    data = {
        "enable_formula": True,
        "enable_table": True,
        "language": LANGUAGE,
        "model_version": model_version,
        "files": files_payload,
    }
    log(f"申请 {len(pdfs)} 个上传 URL (model_version={model_version}, language={LANGUAGE})")
    resp = requests.post(BATCH_URL, headers=HEADERS_JSON, json=data, timeout=60)
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"申请上传 URL 失败: {result}")
    batch_id = result["data"]["batch_id"]
    urls = result["data"]["file_urls"]
    log(f"batch_id = {batch_id}")
    if len(urls) != len(pdfs):
        raise RuntimeError(f"返回 URL 数 {len(urls)} 与 PDF 数 {len(pdfs)} 不一致")
    return batch_id, urls


def upload_pdfs(pdfs: list[Path], urls: list[str]) -> None:
    """PUT 上传 PDF；OSS 预签名 URL 不要带 Authorization header。"""
    for pdf, url in zip(pdfs, urls):
        log(f"上传 {pdf.name} ...")
        with open(pdf, "rb") as f:
            r = requests.put(url, data=f, timeout=600)
        if r.status_code != 200:
            raise RuntimeError(
                f"上传失败 {pdf.name}: status={r.status_code}, body={r.text[:200]}"
            )
        log(f"  ✓ 上传成功 {pdf.name}")


def poll_batch(batch_id: str) -> list[dict]:
    """轮询到所有任务 done/failed 为止。"""
    url = RESULT_URL_TMPL.format(batch_id=batch_id)
    start = time.time()
    while True:
        resp = requests.get(url, headers=HEADERS_JSON, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 0:
            raise RuntimeError(f"查询结果失败: {result}")
        extract_results = result["data"].get("extract_result", [])
        states = [r.get("state") for r in extract_results]
        pending = sum(1 for s in states if s not in ("done", "failed"))
        log(f"  状态: {states} (未完成 {pending}/{len(states)})")
        if pending == 0 and extract_results:
            return extract_results
        if time.time() - start > POLL_TIMEOUT:
            raise TimeoutError(f"轮询超时（>{POLL_TIMEOUT}s），batch_id={batch_id}")
        time.sleep(POLL_INTERVAL)


def download_and_extract(task: dict, cache_root: Path) -> Path | None:
    """下载 full_zip_url 并解压到 cache_root/{paper_id}/。"""
    paper_id = task.get("data_id") or Path(task.get("file_name") or "unknown").stem
    state = task.get("state")
    if state != "done":
        log(f"  ✗ {paper_id} 失败: {task.get('err_msg') or task}")
        return None
    zip_url = task.get("full_zip_url")
    if not zip_url:
        log(f"  ✗ {paper_id} 没有 full_zip_url: {task}")
        return None
    target = cache_root / paper_id
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    log(f"  下载 {paper_id} → {target}")
    r = requests.get(zip_url, timeout=600)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        zf.extractall(target)
    (target / "_mineru_task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


# =========================================================================
# MinerU 输出 → wiki 规范形式
# =========================================================================
def _find_content_list(cache_dir: Path) -> Path | None:
    """找到 *_content_list.json（排除 _v2 变体）。"""
    for f in cache_dir.iterdir():
        name = f.name
        if name.endswith("_content_list.json") and not name.endswith("_content_list_v2.json"):
            return f
    return None


def _page_head_text(content_list: list[dict], max_chars: int = 8000) -> str:
    """拼接 page_idx==0 的 text/title 内容，作为 venue 检测的首页文本。

    MinerU 生成的 content_list 里 type ∈ {text, title, equation, image, table, ...}，
    只取前 2 页的 text/title 已经足够覆盖标题、作者、脚注的 venue 声明。
    """
    parts: list[str] = []
    total = 0
    for b in content_list:
        if b.get("page_idx", 0) > 1:  # 取前两页
            continue
        if b.get("type") not in ("text", "title", "equation"):
            continue
        t = b.get("text") or b.get("content") or ""
        if not t:
            continue
        parts.append(t)
        total += len(t)
        if total >= max_chars:
            break
    return "\n".join(parts)


def _parse_fig_label(caption_list: list[str]) -> str:
    """从 image_caption 中解析 'Figure N' / 'Fig. N' 标签。"""
    if not caption_list:
        return ""
    joined = " ".join(caption_list)
    m = re.search(r"\b(?:Figure|Fig\.?)\s*(\d+)", joined, re.IGNORECASE)
    if m:
        return f"Figure {int(m.group(1))}"
    return ""


def _jpg_to_png(src: Path, dst: Path) -> None:
    """把 MinerU 的 JPG 图转成 PNG；若已经是 PNG 则直接复制。"""
    if src.suffix.lower() == ".png":
        shutil.copy2(src, dst)
        return
    with Image.open(src) as im:
        if im.mode in ("RGBA", "LA"):
            im.save(dst, "PNG")
        else:
            im.convert("RGB").save(dst, "PNG")


def convert_mineru_to_wiki(
    paper_id: str,
    cache_dir: Path,
    sources_dir: Path,
    assets_dir: Path,
    pdf_file: Path,
) -> dict:
    """将 MinerU 解压产物转换为 wiki 规范形式：md + figN.png + _index.json。

    返回：{
      "chars": int, "figures": [...fig_metadata...],
      "metadata": {title,date,arxiv,authors,venue,tags}
    }
    """
    # -- 读取源 --
    md_src = cache_dir / "full.md"
    if not md_src.exists():
        raise RuntimeError(f"{paper_id}: 缺少 full.md")
    md_text = md_src.read_text(encoding="utf-8")

    content_list_path = _find_content_list(cache_dir)
    if not content_list_path:
        raise RuntimeError(f"{paper_id}: 缺少 content_list.json")
    content_list: list[dict] = json.loads(content_list_path.read_text(encoding="utf-8"))

    # -- 清理旧的 fig*.png（force / 覆盖模式下保证干净） --
    paper_fig_dir = assets_dir / paper_id
    paper_fig_dir.mkdir(parents=True, exist_ok=True)
    for old in paper_fig_dir.iterdir():
        if re.fullmatch(r"fig\d+\.png", old.name):
            old.unlink()

    # -- 遍历 content_list 提取 image block，全局顺序命名 figN.png --
    img_blocks = [b for b in content_list if b.get("type") == "image"]
    fig_metadata: list[dict] = []
    name_map: dict[str, str] = {}   # 原始 img_path → fig{N}.png

    for idx, block in enumerate(img_blocks, 1):
        rel = block.get("img_path") or ""
        if not rel:
            continue
        src = cache_dir / rel
        if not src.exists():
            log(f"  [WARN] {paper_id}: 图片缺失 {rel}")
            continue
        new_name = f"fig{idx}.png"
        dst = paper_fig_dir / new_name
        try:
            _jpg_to_png(src, dst)
        except Exception as e:  # noqa: BLE001
            log(f"  [WARN] {paper_id}: 图像转换失败 {rel}: {e}")
            continue

        try:
            with Image.open(dst) as im:
                w, h = im.size
        except Exception:
            w, h = 0, 0

        figure_label = _parse_fig_label(block.get("image_caption") or [])
        meta = {
            "page": int(block.get("page_idx", 0)),
            "figure_index": idx,
            "filename": new_name,
            "path": f"assets/figures/{paper_id}/{new_name}",
            "width": w,
            "height": h,
            "size_kb": round(dst.stat().st_size / 1024, 2),
            "type": "figure",
            "caption": " ".join(block.get("image_caption") or []).strip(),
        }
        if figure_label:
            meta["figure_label"] = figure_label
        fig_metadata.append(meta)
        name_map[rel] = new_name

    # -- 主图筛选：同一 figure_label 的子图只保留面积最大的一张 --
    groups: dict[str, list[dict]] = defaultdict(list)
    for m in fig_metadata:
        if "figure_label" in m:
            groups[m["figure_label"]].append(m)
    for label, items in groups.items():
        if len(items) > 1:
            items.sort(key=lambda x: x["width"] * x["height"], reverse=True)
            for it in items[1:]:
                it.pop("figure_label", None)

    # -- Markdown 图片路径替换：images/xxx.jpg → assets/figures/{paper_id}/figN.png --
    for rel, new_name in name_map.items():
        escaped = re.escape(rel)
        md_text = re.sub(
            r"!\[([^\]]*)\]\(" + escaped + r"\)",
            lambda mo: f"![{mo.group(1)}](assets/figures/{paper_id}/{new_name})",
            md_text,
        )
    # 回收：若仍有残留的 images/xxx 引用（比如 MinerU 额外嵌入但不在 content_list 中），
    # 统一改为同目录下的备份占位，避免破链。（实测基本不触发）
    md_text = re.sub(
        r"!\[([^\]]*)\]\(images/([^)]+)\)",
        lambda mo: f"![{mo.group(1)}](assets/figures/{paper_id}/_unmapped_{mo.group(2).replace('/', '_')})",
        md_text,
    )

    # -- 防御性 LaTeX 定界符修正（MinerU 通常已输出 $..$/$$..$$，多做一次无害） --
    md_text = re.sub(r"\\\[", "$$", md_text)
    md_text = re.sub(r"\\\]", "$$", md_text)
    md_text = re.sub(r"\\\(", "$", md_text)
    md_text = re.sub(r"\\\)", "$", md_text)

    # -- Frontmatter --
    try:
        doc = fitz.open(str(pdf_file))
        pdf_metadata = doc.metadata or {}
        doc.close()
    except Exception:
        pdf_metadata = {}

    # title: 优先 MinerU markdown 首行 #，其次 PDF 元数据
    title = ""
    for line in md_text.strip().split("\n")[:12]:
        line = line.strip()
        if line.startswith("# ") and len(line) > 2:
            title = line.lstrip("#").strip()
            break
    if not title and pdf_metadata.get("title"):
        title = pdf_metadata["title"]

    # date
    date = ""
    if pdf_metadata.get("creationDate"):
        cd = pdf_metadata["creationDate"]
        if isinstance(cd, str) and cd.startswith("D:") and len(cd) >= 10:
            date = cd[2:6] + "-" + cd[6:8]
    if not date:
        fname = pdf_file.stem
        if len(fname) >= 4 and fname[:2] in ("18", "19", "20", "21", "22", "23", "24", "25", "26"):
            date = f"20{fname[:2]}-{fname[2:4]}"

    # authors: 保守从 PDF 元数据取（MinerU 的作者行难以可靠解析）
    authors: list[str] = []
    if pdf_metadata.get("author"):
        author_str = pdf_metadata["author"]
        if ";" in author_str:
            authors = [a.strip() for a in author_str.split(";") if a.strip()]
        elif " and " in author_str:
            authors = [a.strip() for a in author_str.split(" and ") if a.strip()]
        else:
            authors = [author_str.strip()]

    # venue: 基于首页文本 + 文件名
    head_text = _page_head_text(content_list)
    venue = detect_venue(head_text, pdf_stem=pdf_file.stem)

    # tags
    tags = ["paper"]
    tl = (title or "").lower()
    body_lower = md_text[:2000].lower()
    if "recommend" in tl or "recommend" in body_lower:
        tags.append("recommendation")
    if "generative" in tl or "generative" in body_lower:
        tags.append("generative-recommendation")
    if "scaling" in tl or "scaling law" in body_lower:
        tags.append("scaling-law")
    if "sequential" in tl or "sequential" in body_lower:
        tags.append("sequential-recommendation")
    if "ctr" in body_lower or "click-through" in body_lower:
        tags.append("ctr-prediction")
    if "search" in tl or "search" in body_lower:
        tags.append("search")
    if "transformer" in tl or "transformer" in body_lower:
        tags.append("transformer")
    if "attention" in tl or "attention" in body_lower:
        tags.append("attention")

    fm = ["---"]
    if title:
        fm.append(f'title: {json.dumps(title, ensure_ascii=False)}')
    if date:
        fm.append(f"date: {date}")
    fm.append(f"arxiv: {pdf_file.stem}")
    if authors:
        fm.append(f"authors: {json.dumps(authors, ensure_ascii=False)}")
    if venue:
        fm.append(f'venue: "{venue}"')
    fm.append(f"tags: {json.dumps(tags, ensure_ascii=False)}")
    fm.append(f'source_pdf: "{pdf_file.name}"')
    fm.append("extractor: mineru-vlm")
    fm.append("---")
    fm.append("")
    final_content = "\n".join(fm) + md_text

    # -- 写 md --
    md_path = sources_dir / f"{paper_id}.md"
    md_path.write_text(final_content, encoding="utf-8")

    # -- 写 _index.json --
    if fig_metadata:
        index_data = {
            "paper_id": paper_id,
            "total_figures": len(fig_metadata),
            "extractor": "mineru-vlm",
            "figures": fig_metadata,
        }
        if pdf_metadata.get("title"):
            index_data["title"] = pdf_metadata["title"]
        if pdf_metadata.get("author"):
            index_data["author"] = pdf_metadata["author"]
        (paper_fig_dir / "_index.json").write_text(
            json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return {
        "chars": len(final_content),
        "figures": fig_metadata,
        "metadata": {
            "title": title, "date": date, "arxiv": pdf_file.stem,
            "authors": authors, "venue": venue, "tags": tags,
        },
    }


# =========================================================================
# 增量调度
# =========================================================================
def needs_extraction(pdf_file: Path, md_path: Path, figure_dir: Path) -> tuple[bool, str]:
    if not md_path.exists():
        return True, "新增文件"
    if not figure_dir.exists():
        return True, "图片目录缺失"
    if not (figure_dir / "_index.json").exists():
        return True, "索引文件缺失"
    if pdf_file.stat().st_mtime > md_path.stat().st_mtime:
        return True, "PDF 已更新"
    return False, "已是最新"


def run_mineru_batch(pdfs: list[Path], cache_root: Path, model_version: str) -> dict[str, Path]:
    """对一批 PDF 跑完整 MinerU 流程，返回 paper_id → 解压目录。"""
    if not pdfs:
        return {}
    cache_root.mkdir(parents=True, exist_ok=True)
    batch_id, urls = request_upload_urls(pdfs, model_version)
    upload_pdfs(pdfs, urls)
    log("开始轮询解析状态 ...")
    tasks = poll_batch(batch_id)
    log("下载并解压结果 ...")
    result: dict[str, Path] = {}
    for t in tasks:
        path = download_and_extract(t, cache_root)
        if path is not None:
            result[path.name] = path
    return result


def main():
    if not TOKEN:
        print("✗ 错误: MinerU API Token 未配置")
        print("  请设置环境变量: export MINERU_TOKEN=\"your-api-key\"")
        print("  或在脚本中直接设置 TOKEN 变量")
        print("  申请地址: https://mineru.net/apiManage/docs")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="使用 MinerU VLM 将 raw/*.pdf 提取到 wiki/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python extract_pdf_mineru.py              # 增量提取
  python extract_pdf_mineru.py --all        # 全量提取
  python extract_pdf_mineru.py --force 2510.07972v1
  python extract_pdf_mineru.py --list       # 列出状态
  python extract_pdf_mineru.py --keep-raw   # 保留 MinerU 原始解压产物
""",
    )
    parser.add_argument("--all", "-a", action="store_true", help="全量提取")
    parser.add_argument("--force", "-f", metavar="PAPER_ID", help="强制提取指定 paper_id")
    parser.add_argument("--list", "-l", action="store_true", help="列出 PDF 及状态")
    parser.add_argument("--keep-raw", action="store_true",
                        help="保留 MinerU 原始解压产物到 mineru_result/")
    parser.add_argument("--model-version", default=DEFAULT_MODEL_VERSION,
                        choices=["vlm", "pipeline"],
                        help="MinerU 模型版本（默认 vlm）")
    args = parser.parse_args()

    base_dir = Path(__file__).parent.resolve()
    raw_dir = base_dir / "raw"
    sources_dir = base_dir / "wiki" / "sources"
    assets_dir = base_dir / "wiki" / "assets" / "figures"
    # --keep-raw 时使用 mineru_test/，否则用临时 _mineru_cache/ 并在完成后清理
    if args.keep_raw:
        cache_root = base_dir / "mineru_result"
    else:
        cache_root = base_dir / "_mineru_cache"

    sources_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(f for f in raw_dir.iterdir() if f.suffix.lower() == ".pdf")

    # --list
    if args.list:
        print(f"{'状态':<8} {'文件名':<40} {'说明'}")
        print("-" * 80)
        for pdf in pdf_files:
            pid = pdf.stem
            needs, reason = needs_extraction(
                pdf, sources_dir / f"{pid}.md", assets_dir / pid
            )
            status = "待提取" if needs else "已完成"
            print(f"{status:<8} {pdf.name:<40} {reason}")
        return

    # 筛选待处理文件
    files_to_process: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    for pdf in pdf_files:
        pid = pdf.stem
        if args.force:
            if pid == args.force:
                files_to_process.append(pdf)
            continue
        if args.all:
            files_to_process.append(pdf)
            continue
        needs, reason = needs_extraction(
            pdf, sources_dir / f"{pid}.md", assets_dir / pid
        )
        if needs:
            files_to_process.append(pdf)
        else:
            skipped.append((pdf, reason))

    if args.force and not files_to_process:
        print(f"✗ 找不到指定的 PDF '{args.force}'")
        print(f"   可用: {', '.join(p.stem for p in pdf_files)}")
        sys.exit(1)

    mode = "全量" if args.all else (f"强制:{args.force}" if args.force else "增量")
    print(f"模式: {mode}    PDF 总数: {len(pdf_files)}    待处理: {len(files_to_process)}    跳过: {len(skipped)}")
    print("-" * 60)
    if not files_to_process:
        print("✓ 无需提取")
        return

    # 跑 MinerU（一次性批量上传，服务端并行解析）
    try:
        paper_to_cache = run_mineru_batch(files_to_process, cache_root, args.model_version)
    except Exception as e:  # noqa: BLE001
        print(f"✗ MinerU 批量处理失败: {e}")
        sys.exit(1)

    # 本地转换
    success = 0
    total_figs = 0
    for pdf in files_to_process:
        pid = pdf.stem
        cache_dir = paper_to_cache.get(pid)
        print(f"处理: {pdf.name} ... ", end="", flush=True)
        if not cache_dir:
            print("✗ 无 MinerU 产出")
            continue
        try:
            info = convert_mineru_to_wiki(pid, cache_dir, sources_dir, assets_dir, pdf)
            n = len(info["figures"])
            total_figs += n
            success += 1
            venue = info["metadata"].get("venue") or "-"
            print(f"✓ (chars={info['chars']}, figs={n}, venue={venue})")
        except Exception as e:  # noqa: BLE001
            print(f"✗ 转换失败: {e}")

    # 清理临时缓存（若未启用 --keep-raw）
    if not args.keep_raw and cache_root.exists():
        try:
            shutil.rmtree(cache_root)
        except Exception:
            pass

    print("-" * 60)
    print(f"完成: {success}/{len(files_to_process)} 个文件已提取")
    print(f"Markdown 目录: {sources_dir}")
    print(f"图片目录:     {assets_dir}")
    print(f"总计提取图片: {total_figs} 张")


if __name__ == "__main__":
    main()
