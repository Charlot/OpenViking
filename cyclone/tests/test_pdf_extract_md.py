# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
解析 3 个基础测试 PDF → Markdown（文本 + 表格 + 图片）。

输出结构:
    data/<pdf-stem>/<pdf-stem>.md        —— 解析结果
    data/<pdf-stem>/images/*.png         —— 提取的图片，md 中以相对路径引用

本地（pdfplumber）快路的当前最佳实践组合：
1. 表格边界检测：取"真正线段 + 描边矩形 + 细条填充矩形"的双向边，
   再加上大填充矩形（页面背景框，飞书/Chromium 导出风格）的**仅竖边**
   ——背景框横边会制造"整页假表格"，但其竖边恰好是半边框表格
   （外框左边线缺失的飞书表格）首列的左边界，弃之会丢首列；
2. 整页级单列表格启发式过滤兜底（面积 > 75% 且 1 列 ⇒ 背景框误检），
   并丢弃提取结果中的全空列（背景框竖边偶尔引入的幻影空列）；
3. 文本按字符级排除表格区域（page.filter），文本与表格零重叠、零丢失；
4. 文本行 / 表格 / 图片按 Y 坐标混排，保留页面内原始顺序；
5. 图片按 bbox 裁剪渲染为 PNG，过小（图标/装饰）与过大（页面背景）跳过；
6. 全局清理 \x01 空格占位符（Chromium 系 PDF）+ NFKC 归一化。
"""

import io
import re
import unicodedata
from pathlib import Path

import pdfplumber

import logging

logging.getLogger("pdfminer").setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# 基础测试文件
# ---------------------------------------------------------------------------
PDF_PATHS = [
    Path("./data/CyClaw-2pages.pdf"),
    Path("./data/CyClaw用户操作手册.pdf"),
    Path("./data/test-pdf.pdf"),
]

# 表格检测参数
FILL_RECT_MAX_SIZE = 3.0  # pt：短边小于此值的填充矩形视为表格细线，否则视为背景框
FULL_PAGE_AREA_RATIO = 0.75  # 单列表格面积占页面超过此比例视为背景框误检

# 图片提取参数
IMAGE_RESOLUTION = 150  # 渲染 DPI
MIN_IMAGE_SIZE = 20.0  # pt：小于此尺寸的图片（图标/装饰）跳过
MAX_IMAGE_AREA_RATIO = 0.9  # 面积占比超过此值的图片视为页面背景，跳过


# ---------------------------------------------------------------------------
# 表格检测
# ---------------------------------------------------------------------------
def _strict_table_settings(page):
    """构建表格检测参数：排除背景框横边，保留背景框竖边。

    边界对象分两类：
    - 双向边（横+竖）：page.lines（真正画出的线段）、描边矩形
      （stroke=True 或 linewidth>0）、细条填充矩形（短边 < FILL_RECT_MAX_SIZE，
      例如行列分隔线）；
    - 仅竖边：大填充矩形（页面背景框）。其横边位于页眉/页脚位置，
      会把整页闭合成假表格，必须丢弃；但其竖边与正文左右边界重合，
      恰是半边框表格（飞书表格常缺左侧外框线）首列的左边界，需要保留。
    """
    v_objs, h_objs = [], []
    for ln in page.lines:
        (v_objs if ln["top"] != ln["bottom"] else h_objs).append(ln)
    for r in page.rects:
        stroked = bool(r.get("stroke")) or (r.get("linewidth") or 0) > 0
        thin = min(r["width"], r["height"]) < FILL_RECT_MAX_SIZE
        if stroked or thin:
            v_objs.append(r)
            h_objs.append(r)
        else:
            v_objs.append(r)  # 大背景框：仅竖边
    if len(v_objs) < 2 or len(h_objs) < 2:
        # pdfplumber 的 explicit 策略要求每个方向至少 2 条边界；
        # 某方向边界不足 2 个时不可能构成表格，直接跳过检测
        return None
    return {
        "vertical_strategy": "explicit",
        "explicit_vertical_lines": v_objs,
        "horizontal_strategy": "explicit",
        "explicit_horizontal_lines": h_objs,
    }


def _find_real_tables(page):
    """检测真实表格，并过滤"覆盖近乎整页的单列"误检。"""
    settings = _strict_table_settings(page)
    tables = page.find_tables(table_settings=settings) if settings else []

    page_area = page.width * page.height
    real_tables = []
    for table in tables:
        x0, top, x1, bottom = table.bbox
        area_ratio = (x1 - x0) * (bottom - top) / page_area if page_area > 0 else 0
        if area_ratio > FULL_PAGE_AREA_RATIO and len(table.columns) <= 1:
            continue  # 背景框误检：面积过大且只有 1 列
        real_tables.append(table)
    return real_tables


def _drop_empty_cols(rows):
    """丢弃所有行都为空的列（背景框竖边偶尔引入的幻影空列）。"""
    if not rows:
        return rows
    ncols = max(len(r) for r in rows)
    keep = [
        j
        for j in range(ncols)
        if any(j < len(r) and r[j] and str(r[j]).strip() for r in rows)
    ]
    return [[r[j] if j < len(r) else None for j in keep] for r in rows]


def _format_table_markdown(table):
    """pdfplumber 提取的二维表 → Markdown 表格。空表返回 ""。"""
    table = _drop_empty_cols(table)
    if not table or not table[0]:
        return ""

    def clean_cell(cell):
        if cell is None:
            return ""
        return str(cell).replace("|", "\\|").replace("\n", "<br>").strip()

    lines = []
    header = [clean_cell(c) for c in table[0]]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")
    for row in table[1:]:
        padded = list(row) + [None] * (len(header) - len(row))
        cells = [clean_cell(c) for c in padded[: len(header)]]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 文本提取（排除表格区域）
# ---------------------------------------------------------------------------
def _page_outside_tables(page, bboxes):
    """返回剔除表格区域内字符后的页面，供 extract_text_lines 使用。"""

    def keep(obj):
        if obj["object_type"] != "char":
            return True
        cx = (obj["x0"] + obj["x1"]) / 2
        cy = (obj["top"] + obj["bottom"]) / 2
        return not any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in bboxes)

    return page.filter(keep)


# ---------------------------------------------------------------------------
# 图片提取
# ---------------------------------------------------------------------------
def _render_image(page, img):
    """按 bbox 裁剪渲染图片为 PNG bytes；过小/过大/失败返回 None。"""
    try:
        x0 = max(0.0, img["x0"])
        top = max(0.0, img["top"])
        x1 = min(page.width, img["x1"])
        bottom = min(page.height, img["bottom"])
        if x1 - x0 < MIN_IMAGE_SIZE or bottom - top < MIN_IMAGE_SIZE:
            return None  # 图标/装饰
        if (x1 - x0) * (bottom - top) > page.width * page.height * MAX_IMAGE_AREA_RATIO:
            return None  # 页面背景
        image = page.crop((x0, top, x1, bottom)).to_image(resolution=IMAGE_RESOLUTION)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:
        return None


def _release_page_cache(page):
    """释放 pdfplumber/pdfminer 的页级缓存，控制长文档内存占用。"""
    close = getattr(page, "close", None)
    if callable(close):
        try:
            close()
            return
        except Exception:
            pass
    flush = getattr(page, "flush_cache", None)
    if callable(flush):
        try:
            flush()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 单文件解析
# ---------------------------------------------------------------------------
def parse_pdf_to_md(pdf_path: Path):
    """解析单个 PDF，输出 md + 图片到 data/<stem>/，返回 (md_path, stats)。"""
    stem = pdf_path.stem
    out_dir = pdf_path.parent / stem
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{stem}.md"

    parts = [f"# {stem}"]
    stats = {"pages": 0, "tables": 0, "images": 0}

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            try:
                stats["pages"] += 1
                blocks = []  # (top, kind_order, kind, payload)

                # --- 表格 ---
                real_tables = _find_real_tables(page)
                table_bboxes = [t.bbox for t in real_tables]
                for table in real_tables:
                    md_table = _format_table_markdown(table.extract())
                    if md_table:
                        blocks.append((table.bbox[1], 1, "table", md_table))
                        stats["tables"] += 1

                # --- 图片 ---
                for img_idx, img in enumerate(page.images or [], 1):
                    png = _render_image(page, img)
                    if png is None:
                        continue
                    filename = f"page{page_num}_img{img_idx}.png"
                    (img_dir / filename).write_bytes(png)
                    blocks.append(
                        (
                            img["top"],
                            2,
                            "image",
                            f"![Page {page_num} Image {img_idx}](images/{filename})",
                        )
                    )
                    stats["images"] += 1

                # --- 文本（排除表格区域字符，按行成块） ---
                filtered = _page_outside_tables(page, table_bboxes)
                for line in filtered.extract_text_lines():
                    text = line["text"].strip()
                    if text:
                        blocks.append((line["top"], 0, "text", text))

                # --- 按 Y 混排，连续文本行合并为一个块 ---
                page_parts = []
                for _, _, kind, payload in sorted(blocks, key=lambda b: (b[0], b[1])):
                    if kind == "text" and page_parts and page_parts[-1][0] == "text":
                        page_parts[-1][1] += "\n" + payload
                    else:
                        page_parts.append([kind, payload])

                if page_parts:
                    body = "\n\n".join(payload for _, payload in page_parts)
                    parts.append(f"<!-- Page {page_num} -->\n{body}")
            finally:
                _release_page_cache(page)

    markdown = "\n\n".join(parts)
    # 清理 Chromium 系 PDF 的 \x01 空格占位符 + CJK 兼容字符归一化
    markdown = markdown.replace("\x01", " ")
    markdown = unicodedata.normalize("NFKC", markdown)

    md_path.write_text(markdown, encoding="utf-8")
    stats["md_chars"] = len(markdown)
    return md_path, stats


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
def test_parse_pdfs_to_md():
    """解析 3 个基础 PDF → md（含图片），并校验输出不变量。

    断言：
    1. 每个 PDF 产出非空 md；
    2. md 中无残留 \\x01 占位符；
    3. md 中引用的图片全部真实存在于磁盘；
    4. 整个语料至少检出 1 个表格、1 张图片（证明解析链路完整）。
    """
    total = {"tables": 0, "images": 0}

    for pdf_path in PDF_PATHS:
        assert pdf_path.exists(), f"PDF 不存在: {pdf_path}"

        md_path, stats = parse_pdf_to_md(pdf_path)
        content = md_path.read_text(encoding="utf-8")

        # 断言 1：非空输出
        assert len(content) > 100, f"{pdf_path.name}: md 内容过短 ({len(content)} 字符)"

        # 断言 2：无 \x01 残留
        assert "\x01" not in content, f"{pdf_path.name}: md 中残留 \\x01 占位符"

        # 断言 3：图片引用有效
        for rel in re.findall(r"!\[[^\]]*\]\((images/[^)]+)\)", content):
            assert (md_path.parent / rel).exists(), f"{pdf_path.name}: 图片引用失效 {rel}"

        # 断言 4（回归）：半边框表格首列不得丢为表外孤儿文本。
        # CyClaw-2pages 第 2 页的表格左外框线缺失，首列"端/助手页面/后台管理"
        # 必须出现在表格内部（3 列），而非表格后的独立文本行。
        if pdf_path.stem == "CyClaw-2pages":
            assert re.search(r"\|\s*端\s*\|\s*主要使用者\s*\|\s*核心价值\s*\|", content), (
                "半边框表格首列丢失：表格应为 3 列（端 | 主要使用者 | 核心价值）"
            )

        total["tables"] += stats["tables"]
        total["images"] += stats["images"]
        print(
            f"\n{pdf_path.name}: {stats['pages']} 页 → "
            f"{stats['tables']} 表格, {stats['images']} 图片, "
            f"{stats['md_chars']} 字符 → {md_path}"
        )

    # 断言 4：语料级解析能力
    assert total["tables"] >= 1, "未检出任何表格"
    assert total["images"] >= 1, "未提取到任何图片"


if __name__ == "__main__":
    test_parse_pdfs_to_md()
