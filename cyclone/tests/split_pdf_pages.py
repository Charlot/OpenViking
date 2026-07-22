# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Extract first 2 pages from CyClaw PDF into a small test PDF.

Usage:
    cd cyclone/tests
    python split_pdf_2pages.py
"""

from pathlib import Path

PAGES = 5

SRC = Path(__file__).parent / "data" / "CyClaw用户操作手册.pdf"
DST = Path(__file__).parent / "data" / f"CyClaw-{PAGES}pages.pdf"



def split_with_pikepdf():
    import pikepdf

    pdf = pikepdf.open(str(SRC))
    dst = pikepdf.new()
    dst.pages.extend(pdf.pages[:PAGES])
    dst.save(str(DST))



if __name__ == "__main__":
    split_with_pikepdf()
