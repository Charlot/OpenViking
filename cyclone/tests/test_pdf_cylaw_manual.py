# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Test PDF parsing quality for CyClaw user manual.

"""

import io
import os
import re
import time
from collections import Counter
from pathlib import Path
import pdfplumber
import unicodedata
import pytest

from openviking.parse.parsers.pdf import PDFParser
from openviking_cli.utils.config.parser_config import PDFConfig

import logging
logging.getLogger("pdfminer").setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# Path to the real PDF file under test
# ---------------------------------------------------------------------------
# PDF_PATH = Path("./data/CyClaw用户操作手册.pdf")
PDF_PATH = Path("./data/CyClaw-2pages.pdf")


def test_parse_pdf():
    with pdfplumber.open(PDF_PATH) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            print(f"\n{'='*50}")
            print(f"第 {page_num} 页")
            print(f"{'='*50}")
            
            # 1. 提取纯文本
            text = page.extract_text()
            if text:
                print("\n--- 文本内容 ---")
                print(text.strip())
            
            # 2. 提取表格（如果有多个表格）
            tables = page.extract_tables()
            if tables:
                print("\n--- 表格内容 ---")
                for table_idx, table in enumerate(tables):
                    print(f"\n表格 {table_idx + 1}:")
                    for row in table:
                        # 过滤空行
                        if any(cell and str(cell).strip() for cell in row):
                            print(row)
            else:
                # 尝试提取单个表格
                table = page.extract_table()
                if table:
                    print("\n--- 表格内容 ---")
                    for row in table:
                        if any(cell and str(cell).strip() for cell in row):
                            print(row)


def extract_pdf_industrial():
    """简洁版：表格优先，文本兜底"""
    with pdfplumber.open(PDF_PATH) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            # 先尝试表格
            tables = page.extract_tables()
            real_tables = [
                t for t in tables
                if len(t) >= 2 and len(t[0]) >= 2  # 至少2行2列
            ]

            if real_tables:
                # 有真表格：只输出表格，文本跳过（或做区域排除）
                print(real_tables)
            else:
                # 无表格：输出文本
                text = page.extract_text()
                print( text)

if __name__ == "__main__":
    # test_parse_pdf()
    extract_pdf_industrial()

