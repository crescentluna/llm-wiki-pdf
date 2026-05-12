#!/usr/bin/env python3
"""
统一入口脚本：自动选择 MinerU VLM 或 pymupdf4llm 进行 PDF 提取。

选择逻辑:
  1. 检查环境变量 MINERU_TOKEN
  2. 如果 TOKEN 非空 → 使用 extract_pdf_mineru.py (MinerU VLM, 推荐)
  3. 如果 TOKEN 为空 → 使用 extract_pdf_pymupdf.py (本地, 无需网络)

用法:
  python extract_pdf.py              # 增量提取
  python extract_pdf.py --all        # 全量提取
  python extract_pdf.py --force PID  # 强制重新提取
  python extract_pdf.py --list       # 列出状态
  python extract_pdf.py --backend mineru   # 强制使用 MinerU
  python extract_pdf.py --backend pymupdf  # 强制使用 pymupdf4llm
"""

import os
import sys
import subprocess
from pathlib import Path


def get_token() -> str:
    """获取 MinerU API Token，优先环境变量，其次脚本内的 TOKEN 常量。"""
    # 1. 环境变量
    token = os.environ.get("MINERU_TOKEN", "").strip()
    if token:
        return token

    # 2. 检查 extract_pdf_mineru.py 中的 TOKEN 变量
    mineru_script = Path(__file__).parent / "extract_pdf_mineru.py"
    if mineru_script.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("mineru_module", mineru_script)
        mod = importlib.util.module_from_spec(spec)
        # 只读取 TOKEN 变量，不执行 main
        try:
            with open(mineru_script, "r") as f:
                content = f.read()
            # 简单提取 TOKEN = "..." 或 TOKEN = ""
            import re
            match = re.search(r'^TOKEN\s*=\s*["\'](.+?)["\']', content, re.MULTILINE)
            if match:
                return match.group(1).strip()
            # 多行拼接的 TOKEN
            match = re.search(r'^TOKEN\s*=\s*\(\s*"(.+?)"\s*\)', content, re.MULTILINE | re.DOTALL)
            if match:
                val = match.group(1).replace('"\n    "', '').replace('"    "', '').replace('\n', '').strip()
                if val:
                    return val
        except Exception:
            pass

    return ""


def main():
    base_dir = Path(__file__).parent.resolve()
    mineru_script = base_dir / "extract_pdf_mineru.py"
    pymupdf_script = base_dir / "extract_pdf_pymupdf.py"

    # 解析 --backend 参数（如果有）
    args = sys.argv[1:]
    forced_backend = None
    filtered_args = []
    i = 0
    while i < len(args):
        if args[i] == "--backend" and i + 1 < len(args):
            forced_backend = args[i + 1].lower()
            i += 2
        else:
            filtered_args.append(args[i])
            i += 1

    # 确定使用哪个后端
    if forced_backend == "mineru":
        script = mineru_script
        backend_name = "MinerU VLM"
    elif forced_backend == "pymupdf":
        script = pymupdf_script
        backend_name = "pymupdf4llm"
    else:
        # 自动选择
        token = get_token()
        if token:
            script = mineru_script
            backend_name = "MinerU VLM (auto: TOKEN detected)"
        else:
            script = pymupdf_script
            backend_name = "pymupdf4llm (auto: no TOKEN configured)"

    if not script.exists():
        print(f"✗ 错误: 找不到后端脚本 {script.name}")
        print(f"  请确保 {script.name} 在同目录下")
        sys.exit(1)

    print(f"📄 PDF 提取后端: {backend_name}")
    print(f"   脚本: {script.name}")
    print("-" * 60)

    # 透传参数给选中的后端脚本
    cmd = [sys.executable, str(script)] + filtered_args
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
