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
    # Path("./data/CyClaw-2pages.pdf"),
    # Path("./data/CyClaw-5pages.pdf"),
    # Path("./data/CyClaw用户操作手册.pdf"),
    # Path("./data/test-pdf.pdf"),
    Path("./data/test-pdf.pdf"),
]

# 表格检测参数
FILL_RECT_MAX_SIZE = 3.0  # pt：短边小于此值的填充矩形视为表格细线，否则视为背景框
FULL_PAGE_AREA_RATIO = 0.75  # 单列表格面积占页面超过此比例视为背景框误检

# 图片提取参数
IMAGE_RESOLUTION = 150  # 渲染 DPI
MIN_IMAGE_SIZE = 20.0  # pt：小于此尺寸的图片（图标/装饰）跳过
MAX_IMAGE_AREA_RATIO = 0.9  # 面积占比超过此值的图片视为页面背景，跳过

# 文本行重建参数
FULL_WIDTH_TOLERANCE = 14.0  # pt：行右端距页面文本右缘小于此值视为排满整行（标点压缩会让对齐行尾短 0.5–1 字符）
BOX_DRAWING_CHARS = frozenset("─│┌┐└┘├┤┬┴┼━┃┏┓┗┛┣┫┳┻╋═║")
BULLET_MAP = {"•": "- ", "◦": "  - ", "▪": "- ", "‣": "- "}

# 跨页续表参数（与生产 pdf.py 一致：宁缺毋滥，不满足保持原样）
RECOVER_EDGE_TOLERANCE = 2.0  # pt：竖边与列边界对齐容差
STITCH_COL_TOLERANCE = 5.0  # pt：续表列边界对齐容差
STITCH_BOTTOM_GAP = 20.0  # pt：前页表底距内容底超过此值视为自然结束
STITCH_TOP_GAP = 60.0  # pt：续表顶距内容顶超过此值视为新表


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


def _drop_empty_cols_with_idx(rows):
    """_drop_empty_cols 的同时返回保留列索引（供列边界对齐使用）。"""
    if not rows:
        return rows, []
    ncols = max(len(r) for r in rows)
    keep = [
        j
        for j in range(ncols)
        if any(j < len(r) and r[j] and str(r[j]).strip() for r in rows)
    ]
    return [[r[j] if j < len(r) else None for j in keep] for r in rows], keep


def _table_col_bounds(table, keep):
    """表格列边界 x 序列（len = 列数+1），用于跨页续表列对齐与漏行回收。"""
    try:
        cols = []
        for col in table.columns:
            cells = getattr(col, "cells", None) or []
            xs0 = [c[0] for c in cells]
            xs1 = [c[2] for c in cells]
            if xs0:
                cols.append((min(xs0), max(xs1)))
        if not cols:
            return []
        cols.sort()
        bounds = [cols[0][0]] + [x1 for _, x1 in cols]
        if keep:
            bounds = [bounds[0]] + [bounds[j + 1] for j in keep]
        return bounds
    except Exception:
        return []


def _page_content_bounds(page):
    """页面内容区 (top, bottom)：取最大无描边填充矩形（背景框），无则整页。"""
    page_area = page.width * page.height
    best = None
    for r in getattr(page, "rects", []) or []:
        if r.get("stroke") or (r.get("linewidth") or 0) > 0:
            continue
        area = r["width"] * r["height"]
        if area > page_area * 0.5 and (best is None or area > best[0]):
            best = (area, r["top"], r["bottom"])
    if best:
        return best[1], best[2]
    return 0.0, page.height


def _recover_leaked_rows(chars, text_lines, table_dict, page):
    """回收跨页续表漏出的首行（飞书续页首行无顶边框，检测不到表格闭合）。

    硬约束（与生产 pdf.py 一致）：候选行必须被 ≥2 条与列边界对齐、从表体
    向上延伸的竖边实际包围；单列字符行并入上一回收行对应单元格。
    返回 (recovered_rows, consumed_line_idx, region_top)。
    """
    table_top = table_dict["bbox"][1]
    bounds = table_dict.get("col_bounds") or []
    if len(bounds) < 2:
        return [], set(), table_top

    v_edges = []  # (x, top, bottom)
    for ln in getattr(page, "lines", []) or []:
        if ln["top"] == ln["bottom"]:
            continue
        x = (ln["x0"] + ln["x1"]) / 2
        if ln["top"] < table_top - 1 and ln["bottom"] >= table_top - 1:
            if any(abs(x - b) <= RECOVER_EDGE_TOLERANCE for b in bounds):
                v_edges.append((x, ln["top"], ln["bottom"]))
    for r in getattr(page, "rects", []) or []:
        if r["width"] >= FILL_RECT_MAX_SIZE or r["height"] < FILL_RECT_MAX_SIZE:
            continue
        x = (r["x0"] + r["x1"]) / 2
        if r["top"] < table_top - 1 and r["bottom"] >= table_top - 1:
            if any(abs(x - b) <= RECOVER_EDGE_TOLERANCE for b in bounds):
                v_edges.append((x, r["top"], r["bottom"]))
    if len({round(x, 1) for x, _, _ in v_edges}) < 2:
        return [], set(), table_top
    region_top = min(t for _, t, _ in v_edges)

    recovered = []
    consumed = set()
    for idx, line in enumerate(text_lines):
        if line["bottom"] > table_top + 1 or line["top"] < region_top - 2:
            continue
        covering = [
            e for e in v_edges if e[1] <= line["top"] + 2 and e[2] >= line["bottom"] - 2
        ]
        if len({round(x, 1) for x, _, _ in covering}) < 2:
            continue
        cells = [[] for _ in range(len(bounds) - 1)]
        for c in chars:
            if c["top"] >= line["bottom"] or c["bottom"] <= line["top"]:
                continue
            cx = (c["x0"] + c["x1"]) / 2
            for j in range(len(cells)):
                if bounds[j] - 1 <= cx <= bounds[j + 1] + 1:
                    cells[j].append(c)
                    break
        texts = [
            "".join(ch["text"] for ch in sorted(cs, key=lambda c: c["x0"]))
            .replace("\x01", " ")
            .strip()
            for cs in cells
        ]
        non_empty = [j for j, t in enumerate(texts) if t]
        if len(non_empty) >= 2:
            recovered.append(texts)
            consumed.add(idx)
        elif len(non_empty) == 1 and recovered:
            j = non_empty[0]
            recovered[-1][j] = _join_two_lines(recovered[-1][j], texts[j])
            consumed.add(idx)
    return recovered, consumed, region_top


def _norm_row(row):
    return [re.sub(r"\s+", "", c or "") for c in row]


def _stitch_cross_page_tables(pages):
    """跨页续表合并（与生产 pdf.py 一致的三重几何守卫，不满足保持原样）。"""
    for i in range(1, len(pages)):
        cur = pages[i]
        # 最近一个仍有块的前页（块为空的页说明其表已被并入更早的页——链式合并）
        j = i - 1
        while j >= 0 and not pages[j]["blocks"]:
            j -= 1
        if j < 0:
            continue
        prev = pages[j]
        if not cur["blocks"]:
            continue
        if prev["blocks"][-1][0] != "table" or cur["blocks"][0][0] != "table":
            continue
        p, t = prev["blocks"][-1][1], cur["blocks"][0][1]
        pb, tb = p.get("col_bounds") or [], t.get("col_bounds") or []
        if not pb or len(pb) != len(tb):
            continue
        if any(abs(a - b) > STITCH_COL_TOLERANCE for a, b in zip(pb, tb)):
            continue
        end_idx = p.get("end_idx", j)
        if pages[end_idx]["content_bottom"] - p["bbox"][3] > STITCH_BOTTOM_GAP:
            continue
        if t["top"] - cur["content_top"] > STITCH_TOP_GAP:
            continue
        rows = t["rows"]
        if rows and p["rows"] and _norm_row(rows[0]) == _norm_row(p["rows"][0]):
            rows = rows[1:]
        p["rows"].extend(rows)
        p["bbox"] = (p["bbox"][0], p["bbox"][1], p["bbox"][2], t["bbox"][3])
        p["end_idx"] = i
        cur["blocks"].pop(0)


def _join_cell_lines(text):
    """合并单元格内换行（与生产 pdf.py 同规则）：CJK 之间直接相连，其余补空格。"""
    out = ""
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        out = _join_two_lines(out, ln)
    return out


def _join_two_lines(a, b):
    """按 CJK 规则拼接两行：两端都是宽字符直接相连，否则补空格。"""
    if a and not (
        unicodedata.east_asian_width(a[-1]) in ("W", "F")
        and unicodedata.east_asian_width(b[0]) in ("W", "F")
    ):
        return a + " " + b
    return a + b


def _normalize_list_line(text):
    """PDF 项目符号 / 有序编号 → Markdown 列表行。返回 (is_list, text)。"""
    for bullet, md in BULLET_MAP.items():
        if text.startswith(bullet):
            return True, md + text[1:].strip()
    if re.match(r"^\d+\.\s", text):
        return True, text
    return False, text


def _starts_new_block(text):
    """是否强制另起逻辑行："1.5 ⻚面导航关系" 类章节编号行（非列表，但不可并入段落）。"""
    return bool(re.match(r"^\d+(\.\d+)+\s", text))


def _reconstruct_text_lines(lines, right_edge):
    """连续视觉行 → Markdown 逻辑结构（与生产 pdf.py 同规则）。

    1. 含 box-drawing 字符的连续行（ASCII 树/代码块，含相邻 "N " 行号行）→ ``` 围栏；
    2. 项目符号与 "N." 有序项 → Markdown 列表，连续列表项单行相连；
    3. 段落软换行合并：上一视觉行排满整行（右端贴近 right_edge）时当前行是续行，
       按 CJK 规则并入；逻辑行之间以空行分隔。
    """
    # \x01（Chromium 排版间隙占位符）先归一为空格，否则行首模式匹配失效
    norm = [
        dict(l, text=l["text"].replace("\x01", " ").strip())
        for l in lines
        if l["text"].strip()
    ]
    if not norm:
        return ""

    raw_code = [
        bool(BOX_DRAWING_CHARS.intersection(l["text"]))
        or bool(re.match(r"^\d{1,3}\s+\S", l["text"]))
        for l in norm
    ]
    code = [False] * len(norm)
    i = 0
    while i < len(norm):
        if not raw_code[i]:
            i += 1
            continue
        j = i
        has_box = False
        while j < len(norm) and raw_code[j]:
            has_box = has_box or bool(BOX_DRAWING_CHARS.intersection(norm[j]["text"]))
            j += 1
        if has_box and j - i >= 2:
            for k in range(i, j):
                code[k] = True
        i = j

    blocks = []  # [kind, text]，kind ∈ code | list | para
    prev_full = False
    for idx, line in enumerate(norm):
        text = line["text"]
        if code[idx]:
            if blocks and blocks[-1][0] == "code":
                blocks[-1][1] += "\n" + text
            else:
                blocks.append(["code", text])
            prev_full = False
            continue
        is_list, text = _normalize_list_line(text)
        is_full = line.get("x1", 0.0) >= right_edge - FULL_WIDTH_TOLERANCE
        if (
            prev_full
            and not is_list
            and not _starts_new_block(text)
            and blocks
            and blocks[-1][0] in ("para", "list")
        ):
            head, sep, last = blocks[-1][1].rpartition("\n")
            blocks[-1][1] = head + sep + _join_two_lines(last, text)
        elif is_list and blocks and blocks[-1][0] == "list":
            blocks[-1][1] += "\n" + text
        else:
            blocks.append(["list" if is_list else "para", text])
        prev_full = is_full

    out = []
    for kind, text in blocks:
        out.append(f"```\n{text}\n```" if kind == "code" else text)
    return "\n\n".join(out)


def _format_table_markdown(table):
    """pdfplumber 提取的二维表 → Markdown 表格。空表返回 ""。"""
    table = _drop_empty_cols(table)
    if not table or not table[0]:
        return ""

    def clean_cell(cell):
        if cell is None:
            return ""
        text = str(cell).replace("|", "\\|").strip()
        return _join_cell_lines(text) if "\n" in text else text

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
    pages = []  # 逐页结构化块：先收集，跨页续表缝合后再渲染

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            try:
                stats["pages"] += 1
                blocks = []  # (top, kind_order, kind, payload)

                # --- 表格（保留结构化 rows/col_bounds，缝合后渲染） ---
                real_tables = _find_real_tables(page)
                table_bboxes = [t.bbox for t in real_tables]
                table_dicts = []
                for table in real_tables:
                    rows, keep = _drop_empty_cols_with_idx(table.extract())
                    if not rows or not rows[0]:
                        continue
                    table_dicts.append(
                        {
                            "rows": rows,
                            "bbox": table.bbox,
                            "col_bounds": _table_col_bounds(table, keep),
                            "top": table.bbox[1],
                        }
                    )
                table_dicts.sort(key=lambda td: td["bbox"][1])
                for td in table_dicts:
                    blocks.append((td["bbox"][1], 1, "table", td))

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
                text_lines = filtered.extract_text_lines()
                right_edge = max((l["x1"] for l in text_lines), default=0.0)
                # --- 漏行回收：跨页续表首行无顶边框检测不到，
                # 若被列竖边向上延伸包围则按列聚回并 prepend 到首表 ---
                consumed = set()
                if text_lines and table_dicts:
                    try:
                        recovered, consumed, region_top = _recover_leaked_rows(
                            getattr(filtered, "chars", []),
                            text_lines,
                            table_dicts[0],
                            page,
                        )
                        if recovered:
                            td = table_dicts[0]
                            td["rows"] = recovered + td["rows"]
                            td["top"] = min(td["top"], region_top)
                    except Exception:
                        consumed = set()
                for idx, line in enumerate(text_lines):
                    if idx in consumed:
                        continue
                    if line["text"].strip():
                        blocks.append((line["top"], 0, "text", line))

                # --- 按 Y 混排，连续文本行重建为逻辑结构 ---
                # （段落软换行合并 / 项目符号列表 / box-drawing 代码围栏）
                page_parts = []
                text_run = []
                for _, _, kind, payload in sorted(blocks, key=lambda b: (b[0], b[1])):
                    if kind == "text":
                        text_run.append(payload)
                        continue
                    if text_run:
                        page_parts.append(
                            ["text", _reconstruct_text_lines(text_run, right_edge)]
                        )
                        text_run = []
                    page_parts.append([kind, payload])
                if text_run:
                    page_parts.append(["text", _reconstruct_text_lines(text_run, right_edge)])

                content_top, content_bottom = _page_content_bounds(page)
                pages.append(
                    {
                        "page_num": page_num,
                        "blocks": page_parts,
                        "content_top": content_top,
                        "content_bottom": content_bottom,
                    }
                )
            finally:
                _release_page_cache(page)

    # --- 跨页续表缝合（三重几何守卫，不满足保持原样） ---
    _stitch_cross_page_tables(pages)

    # --- 渲染 ---
    for entry in pages:
        if not entry["blocks"]:
            continue
        rendered = []
        table_idx = 0
        for kind, payload in entry["blocks"]:
            if kind == "table":
                md_table = _format_table_markdown(payload["rows"])
                if not md_table:
                    continue
                table_idx += 1
                stats["tables"] += 1
                rendered.append(
                    f"<!-- Page {entry['page_num']} Table {table_idx} -->\n{md_table}"
                )
            else:
                rendered.append(payload)
        if rendered:
            parts.append(f"<!-- Page {entry['page_num']} -->\n" + "\n\n".join(rendered))

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
