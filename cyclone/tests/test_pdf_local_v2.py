# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for PDFParser local conversion V2.

设计文档: cyclone/docs/pdf-parser-v2-design.md

V2 针对飞书/Chromium 导出 PDF 的整页背景框问题：
- 严格表格边界检测（背景框弃横边留竖边）+ 整页单列误检过滤 + 丢弃全空列；
- 文本按字符级排除表格区域（page.filter），文本/表格零重叠；
- 文本行/表格/图片按 Y 混排；图片过滤图标与背景。

V1（local_version="v1"，默认）行为不受影响。
"""

import contextlib
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openviking.parse.parsers.pdf import PDFParser
from openviking_cli.utils.config.parser_config import PDFConfig

# 语料基准（与 test_pdf_extract_md.py 独立实现交叉验证过）
CORPUS_DIR = Path(__file__).resolve().parent / "data"
CORPUS_EXPECT = {
    "CyClaw-2pages": {"tables": 1, "images": 0},
    "CyClaw用户操作手册": {"tables": 85, "images": 54},  # 93 - 8 处跨页续表合并
    "test-pdf": {"tables": 4, "images": 40},
}


def _rect(x0, top, x1, bottom, *, stroke=False, linewidth=0):
    return {
        "x0": x0,
        "top": top,
        "x1": x1,
        "bottom": bottom,
        "width": x1 - x0,
        "height": bottom - top,
        "stroke": stroke,
        "linewidth": linewidth,
    }


class _StubStorage:
    """最小存储桩：接口与 openviking_cli.utils.storage 一致。"""

    def __init__(self, root: Path):
        self.media_dir = Path(root)

    def save_image(self, resource_name, data, filename=None):
        path = self.media_dir / resource_name / f"{filename}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class TestRecoverLeakedRows:
    """跨页续表漏行回收：竖边包围是硬约束，正文段落不可能误回收。"""

    def setup_method(self):
        self.parser = PDFParser()
        self.table = {
            "rows": [["概念", "说明", "位置"], ["MCP 服务器", "用 MCP 接入", "设置-工具"]],
            "bbox": (29.0, 75.0, 556.0, 306.0),
            "col_bounds": [29.0, 113.0, 334.0, 556.0],
            "top": 75.0,
        }
        # 三条列竖边从表体向上延伸到 y=27.7（续页首行无顶边框的典型形态）
        self.page = SimpleNamespace(
            lines=[],
            rects=[
                {"x0": 112.5, "x1": 113.2, "top": 27.7, "bottom": 75.0, "width": 0.7, "height": 47.3},
                {"x0": 333.7, "x1": 334.5, "top": 27.7, "bottom": 75.0, "width": 0.8, "height": 47.3},
                {"x0": 555.0, "x1": 555.7, "top": 27.7, "bottom": 75.0, "width": 0.7, "height": 47.3},
            ],
        )

    @staticmethod
    def _char(text, x, top=35.2, bottom=47.0):
        return {"text": text, "x0": x, "x1": x + 6, "top": top, "bottom": bottom}

    def _lines(self):
        return [
            {"text": "频 说 位", "x0": 34.0, "x1": 400.0, "top": 35.2, "bottom": 47.0},
            {"text": "口", "x0": 118.9, "x1": 131.7, "top": 52.5, "bottom": 64.0},
        ]

    def test_recovers_enclosed_row_and_wrap(self):
        chars = [
            self._char("频", 34.0), self._char("说", 120.0), self._char("位", 340.0),
            self._char("口", 118.9, top=52.5, bottom=64.0),
        ]
        rows, consumed, region_top = self.parser._recover_leaked_rows(
            chars, self._lines(), self.table, self.page
        )
        assert rows == [["频", "说口", "位"]]  # 单列折行 CJK 并入上一行对应单元格
        assert consumed == {0, 1}
        assert region_top == 27.7

    def test_no_recovery_without_vertical_enclosure(self):
        """表格正上方的正文段落（无竖边包围）不得回收。"""
        page = SimpleNamespace(lines=[], rects=[])
        chars = [self._char("正", 34.0), self._char("文", 120.0), self._char("字", 340.0)]
        rows, consumed, _ = self.parser._recover_leaked_rows(
            chars, self._lines(), self.table, page
        )
        assert rows == [] and consumed == set()

    def test_single_wrap_line_without_recovered_row_not_consumed(self):
        """单列行前面没有已回收行时保持正文（防页眉误并入）。"""
        chars = [self._char("口", 118.9, top=52.5, bottom=64.0)]
        lines = [self._lines()[1]]
        rows, consumed, _ = self.parser._recover_leaked_rows(
            chars, lines, self.table, self.page
        )
        assert rows == [] and consumed == set()


class TestStitchCrossPageTables:
    """跨页续表合并：三重几何守卫，任一不满足保持原样。"""

    def setup_method(self):
        self.parser = PDFParser()

    @staticmethod
    def _table(rows, bbox, bounds, top=None):
        return {
            "rows": rows,
            "bbox": bbox,
            "col_bounds": bounds,
            "top": top if top is not None else bbox[1],
        }

    def _pages(self, t1, t2, bottom1=813.7, top2=27.7):
        return [
            {"page_num": 1, "headings": [], "blocks": [["table", t1]],
             "content_top": 27.7, "content_bottom": bottom1},
            {"page_num": 2, "headings": [], "blocks": [["table", t2]],
             "content_top": top2, "content_bottom": 813.7},
        ]

    def test_merges_when_all_guards_pass(self):
        t1 = self._table([["概念", "说明"], ["智能体", "AI 助手"]], (29, 600, 556, 811), [29, 113, 556])
        t2 = self._table([["频道", "接入外部协作"], ["库", "文件视图"]], (29, 75, 556, 306), [29, 113, 556], top=27.7)
        pages = self._pages(t1, t2)
        self.parser._stitch_cross_page_tables(pages)
        assert pages[0]["blocks"][0][1]["rows"] == [
            ["概念", "说明"], ["智能体", "AI 助手"], ["频道", "接入外部协作"], ["库", "文件视图"],
        ]
        assert pages[1]["blocks"] == []

    def test_rejects_when_prev_table_not_cut_off(self):
        """前页表自然结束（表底远离内容底）→ 同模板相邻表也不合并。"""
        t1 = self._table([["概念", "说明"], ["智能体", "AI 助手"]], (29, 300, 556, 500), [29, 113, 556])
        t2 = self._table([["频道", "接入外部协作"]], (29, 75, 556, 306), [29, 113, 556], top=27.7)
        pages = self._pages(t1, t2)
        self.parser._stitch_cross_page_tables(pages)
        assert len(pages[0]["blocks"][0][1]["rows"]) == 2
        assert len(pages[1]["blocks"]) == 1

    def test_rejects_when_continuation_not_at_page_top(self):
        t1 = self._table([["概念", "说明"], ["智能体", "AI 助手"]], (29, 600, 556, 811), [29, 113, 556])
        t2 = self._table([["频道", "接入外部协作"]], (29, 200, 556, 306), [29, 113, 556], top=200)
        pages = self._pages(t1, t2)
        self.parser._stitch_cross_page_tables(pages)
        assert len(pages[1]["blocks"]) == 1

    def test_rejects_when_columns_misaligned(self):
        t1 = self._table([["概念", "说明"], ["智能体", "AI 助手"]], (29, 600, 556, 811), [29, 113, 556])
        t2 = self._table([["频道", "接入外部协作"]], (29, 75, 556, 306), [29, 200, 556], top=27.7)
        pages = self._pages(t1, t2)
        self.parser._stitch_cross_page_tables(pages)
        assert len(pages[1]["blocks"]) == 1

    def test_drops_repeated_header(self):
        """续页重复表头（每页重复表头的 PDF）合并时丢弃。"""
        t1 = self._table([["概念", "说明"], ["智能体", "AI 助手"]], (29, 600, 556, 811), [29, 113, 556])
        t2 = self._table([["概念", "说明"], ["频道", "接入外部协作"]], (29, 75, 556, 306), [29, 113, 556], top=27.7)
        pages = self._pages(t1, t2)
        self.parser._stitch_cross_page_tables(pages)
        assert pages[0]["blocks"][0][1]["rows"] == [
            ["概念", "说明"], ["智能体", "AI 助手"], ["频道", "接入外部协作"],
        ]

    def test_three_page_chain(self):
        bounds = [29, 113, 556]
        t1 = self._table([["h1", "h2"], ["a", "b"]], (29, 600, 556, 811), bounds)
        t2 = self._table([["c", "d"]], (29, 75, 556, 811), bounds, top=27.7)
        t3 = self._table([["e", "f"]], (29, 75, 556, 306), bounds, top=27.7)
        pages = self._pages(t1, t2) + [
            {"page_num": 3, "headings": [], "blocks": [["table", t3]],
             "content_top": 27.7, "content_bottom": 813.7}
        ]
        self.parser._stitch_cross_page_tables(pages)
        assert pages[0]["blocks"][0][1]["rows"] == [["h1", "h2"], ["a", "b"], ["c", "d"], ["e", "f"]]
        assert pages[1]["blocks"] == [] and pages[2]["blocks"] == []


class TestLocalVersionConfig:
    """local_version 配置：默认值、校验、分发。"""

    def test_default_is_v2(self):
        assert PDFConfig().local_version == "v2"

    def test_default_flat_layout(self):
        """PDF 默认平铺一级目录（标题层级启发式不可靠，嵌套会产生同名多级目录）。"""
        assert PDFConfig().flat_layout is True

    def test_invalid_value_rejected(self):
        with pytest.raises(ValueError, match="local_version"):
            PDFConfig(local_version="v3").validate()

    def test_v2_dispatches_to_v2_method(self):
        parser = PDFParser(PDFConfig(strategy="local", local_version="v2"))
        with patch.object(
            parser, "_convert_local_sync_v2", return_value=("md", {})
        ) as mock_v2:
            result = parser._convert_local_sync(Path("dummy.pdf"), storage=MagicMock())
        mock_v2.assert_called_once()
        assert result == ("md", {})

    def test_v1_never_dispatches_to_v2(self):
        parser = PDFParser(PDFConfig(strategy="local", local_version="v1"))  # 显式回退 v1
        fake_pdf = SimpleNamespace(pages=[])
        fake_pdfplumber = SimpleNamespace(
            open=lambda _path: contextlib.nullcontext(fake_pdf)
        )
        with (
            patch(
                "openviking.parse.parsers.pdf.lazy_import",
                return_value=fake_pdfplumber,
            ),
            patch.object(parser, "_extract_bookmarks", return_value=[]),
            patch.object(parser, "_detect_headings_by_font", return_value=[]),
            patch.object(parser, "_convert_local_sync_v2") as mock_v2,
        ):
            parser._convert_local_sync(Path("dummy.pdf"), storage=MagicMock())
        mock_v2.assert_not_called()


class TestReconstructTextLines:
    """文本行重建：段落软换行合并 / 项目符号列表 / box-drawing 代码围栏。"""

    def setup_method(self):
        self.parser = PDFParser()

    @staticmethod
    def _lines(*specs):
        """spec: (text, x1)。right_edge 取 max(x1)。"""
        return [{"text": t, "x1": x} for t, x in specs]

    def test_paragraph_soft_wrap_merged_cjk(self):
        """排满整行的段落续行按 CJK 规则并入上一行（源文件不再断词）。"""
        lines = self._lines(("它同时提供面向终端用戶的助手工作台，以及面向", 500), ("管理员的平台治理后台。", 200))
        md = self.parser._reconstruct_text_lines(lines, right_edge=500)
        assert md == "它同时提供面向终端用戶的助手工作台，以及面向管理员的平台治理后台。"

    def test_short_lines_not_merged(self):
        """未排满整行的逻辑行（元信息）保持独立，空行分隔。"""
        lines = self._lines(("适用系统:CyClaw 0.2 平台", 260), ("适用⻆色:普通用戶", 200))
        md = self.parser._reconstruct_text_lines(lines, right_edge=500)
        assert md == "适用系统:CyClaw 0.2 平台\n\n适用⻆色:普通用戶"

    def test_latin_continuation_gets_space(self):
        lines = self._lines(("using the CyClaw", 500), ("platform daily", 150))
        md = self.parser._reconstruct_text_lines(lines, right_edge=500)
        assert md == "using the CyClaw platform daily"

    def test_bullets_become_markdown_list(self):
        lines = self._lines(("• 1. 系统概述", 200), ("◦ 1.1 什么是 CyClaw", 220), ("• 2. 首次注册", 200))
        md = self.parser._reconstruct_text_lines(lines, right_edge=500)
        assert md == "- 1. 系统概述\n  - 1.1 什么是 CyClaw\n- 2. 首次注册"

    def test_ordered_items_not_swallowed_by_full_width_prev(self):
        """有序列表项不得因上一行排满整行而被并入。"""
        lines = self._lines(("管理员日常使用的完整路径说明文字", 500), ("1. 在后台管理进入后台", 300), ("2. 在数据统计查看指标", 300))
        md = self.parser._reconstruct_text_lines(lines, right_edge=500)
        assert "\n1. 在后台管理进入后台\n2. 在数据统计查看指标" in md

    def test_box_drawing_fenced(self):
        lines = self._lines(
            ("代码块", 60),
            ("1 CyClaw 系统入口", 200),
            ("2 ├─ 注册 / 登录", 200),
            ("3 │ └─ 等待管理员审批", 200),
        )
        md = self.parser._reconstruct_text_lines(lines, right_edge=500)
        assert "代码块\n\n```\n1 CyClaw 系统入口\n2 ├─ 注册 / 登录\n3 │ └─ 等待管理员审批\n```" == md

    def test_isolated_numbered_line_not_fenced(self):
        """无 box-drawing 字符的行号行不构成代码块。"""
        lines = self._lines(("1 第一名", 100), ("2 第二名", 100))
        md = self.parser._reconstruct_text_lines(lines, right_edge=500)
        assert "```" not in md

    def test_section_number_line_not_merged_into_paragraph(self):
        """"1.5 ⻚面导航关系" 类章节编号行不得并入上一排满整行的段落。
        注意 Chromium PDF 中行内空格是 \\x01，须先归一。"""
        lines = self._lines(
            ("管理员账号在助手⻚面左下⻆可⻅后台管理入口的完整说明", 500),
            ("1.5\x01⻚面导航关系", 200),
        )
        md = self.parser._reconstruct_text_lines(lines, right_edge=500)
        assert md.endswith("\n\n1.5 ⻚面导航关系")

    def test_empty_input(self):
        assert self.parser._reconstruct_text_lines([], right_edge=500) == ""


class TestFormatTableMarkdown:
    """单元格内换行的 Markdown 表格渲染（裸换行会破坏表格行）。"""

    def setup_method(self):
        self.parser = PDFParser()

    def _render(self, table):
        return self.parser._format_table_markdown(table)

    def test_cjk_soft_wrap_joined_without_space(self):
        """CJK 之间的软换行直接相连（"使用技\n能" → "使用技能"）。"""
        md = self._render([["端", "核心价值"], ["助手页面", "使用技\n能、查看产物库"]])
        assert "使用技能、查看产物库" in md

    def test_latin_wrap_joined_with_space(self):
        """拉丁词之间的换行补空格，避免粘连。"""
        md = self._render([["feature"], ["hello\nworld"]])
        assert "hello world" in md

    def test_cjk_latin_boundary_gets_space(self):
        md = self._render([["x"], ["使用\nCyClaw"]])
        assert "使用 CyClaw" in md

    def test_pipe_escaped_and_newline_handled(self):
        md = self._render([["a"], ["x|y\nz"]])
        assert "x\\|y z" in md

    def test_none_cell_becomes_empty(self):
        md = self._render([["a", "b"], [None, "x"]])
        assert "|  | x |" in md

    def test_no_raw_newline_inside_table_rows(self):
        md = self._render([["h1", "h2"], ["第一\n行", "第二\n行"]])
        for line in md.splitlines():
            assert line.startswith("|") and line.endswith("|")


class TestDropEmptyCols:
    def setup_method(self):
        self.parser = PDFParser()

    def test_drops_all_empty_column(self):
        rows = [["a", None, "b"], ["c", "", "d"]]
        assert self.parser._drop_empty_cols(rows) == [["a", "b"], ["c", "d"]]

    def test_keeps_partially_filled_column(self):
        rows = [["a", None], ["b", "x"]]
        assert self.parser._drop_empty_cols(rows) == [["a", None], ["b", "x"]]

    def test_empty_input(self):
        assert self.parser._drop_empty_cols([]) == []


class TestStrictTableSettings:
    """背景框弃横边留竖边的边界分类规则。"""

    def setup_method(self):
        self.parser = PDFParser()

    def test_large_fill_rect_vertical_only(self):
        page = SimpleNamespace(
            lines=[],
            rects=[
                _rect(0, 0, 540, 786),  # 整页背景框（纯填充）
                _rect(0, 100, 540, 101),  # 细条分隔线 1
                _rect(0, 200, 540, 201),  # 细条分隔线 2（横边需 ≥2 个对象）
            ],
        )
        settings = self.parser._strict_table_settings(page)
        assert settings is not None
        v, h = settings["explicit_vertical_lines"], settings["explicit_horizontal_lines"]
        big, thin = page.rects[0], page.rects[1]
        assert big in v and big not in h  # 背景框：仅竖边
        assert thin in v and thin in h  # 细条分隔线：双向边

    def test_stroked_rect_bidirectional(self):
        page = SimpleNamespace(
            lines=[],
            rects=[_rect(0, 0, 100, 100, stroke=True), _rect(200, 0, 300, 100, linewidth=1)],
        )
        settings = self.parser._strict_table_settings(page)
        for r in page.rects:
            assert r in settings["explicit_vertical_lines"]
            assert r in settings["explicit_horizontal_lines"]

    def test_returns_none_when_insufficient_boundaries(self):
        page = SimpleNamespace(lines=[], rects=[_rect(0, 0, 540, 786)])
        assert self.parser._strict_table_settings(page) is None


class TestFindRealTables:
    """整页级单列误检过滤 + 异常回退。"""

    def setup_method(self):
        self.parser = PDFParser()

    def _table(self, bbox, ncols):
        table = MagicMock()
        table.bbox = bbox
        table.columns = [object()] * ncols
        return table

    def test_full_page_single_column_filtered(self):
        page = MagicMock(width=100, height=100)
        normal = self._table((10, 10, 50, 50), 2)
        fullpage = self._table((2, 2, 98, 98), 1)  # 面积 92% 且单列
        fullpage_2col = self._table((2, 2, 98, 98), 2)  # 面积 92% 但 2 列 → 保留
        page.find_tables.return_value = [normal, fullpage, fullpage_2col]

        with patch.object(self.parser, "_strict_table_settings", return_value={"x": 1}):
            real = self.parser._find_real_tables(page)

        assert real == [normal, fullpage_2col]

    def test_fallback_to_default_on_exception(self):
        page = MagicMock(width=100, height=100)
        normal = self._table((10, 10, 50, 50), 2)
        page.find_tables.side_effect = [RuntimeError("boom"), [normal]]

        with patch.object(self.parser, "_strict_table_settings", return_value={"x": 1}):
            real = self.parser._find_real_tables(page)

        assert real == [normal]
        assert page.find_tables.call_count == 2  # 严格失败 → 默认重试


class TestIsContentImage:
    def setup_method(self):
        self.parser = PDFParser()
        self.page = SimpleNamespace(width=600.0, height=800.0)

    def test_small_icon_skipped(self):
        img = {"x0": 10, "top": 10, "x1": 25, "bottom": 25}  # 15pt
        assert self.parser._is_content_image(self.page, img) is False

    def test_full_page_background_skipped(self):
        img = {"x0": 0, "top": 0, "x1": 599, "bottom": 799}  # ~99.8%
        assert self.parser._is_content_image(self.page, img) is False

    def test_normal_image_kept(self):
        img = {"x0": 50, "top": 100, "x1": 550, "bottom": 400}
        assert self.parser._is_content_image(self.page, img) is True


class TestPageOutsideTables:
    def setup_method(self):
        self.parser = PDFParser()

    def test_chars_inside_table_removed(self):
        def char(x0, top, x1, bottom):
            return {
                "object_type": "char",
                "x0": x0,
                "top": top,
                "x1": x1,
                "bottom": bottom,
            }

        objs = {
            "inside": char(20, 20, 30, 30),  # 中心 (25,25) 在表格内
            "outside": char(60, 60, 70, 70),  # 中心 (65,65) 在表格外
            "non_char": {"object_type": "line"},
        }
        kept = []

        def fake_filter(pred):
            kept.extend(o for o in objs.values() if pred(o))
            return "filtered-page"

        page = SimpleNamespace(filter=fake_filter)
        result = self.parser._page_outside_tables(page, [(10, 10, 50, 50)])

        assert result == "filtered-page"
        assert objs["inside"] not in kept
        assert objs["outside"] in kept
        assert objs["non_char"] in kept


class TestCollectHeadingsByPage:
    """V1/V2 共用的标题检测分组（从 V1 重构抽出）。"""

    def test_meta_updated_and_grouped(self):
        parser = PDFParser()
        meta = {
            "bookmarks_found": 0,
            "bookmarks_resolved": 0,
            "bookmarks_unresolved": 0,
            "headings_found": 0,
            "heading_source": "none",
        }
        with patch.object(
            parser,
            "_extract_bookmarks",
            return_value=[
                {"level": 1, "title": "A", "page_num": 1},
                {"level": 2, "title": "B", "page_num": 1},
                {"level": 1, "title": "C", "page_num": None},
                {"level": 1, "title": "D", "page_num": 3},
            ],
        ):
            by_page = parser._collect_headings_by_page(MagicMock(), meta)

        assert sorted(by_page.keys()) == [1, 3]
        assert [b["title"] for b in by_page[1]] == ["A", "B"]
        assert meta["bookmarks_found"] == 4
        assert meta["bookmarks_resolved"] == 3
        assert meta["bookmarks_unresolved"] == 1
        assert meta["headings_found"] == 3
        assert meta["heading_source"] == "bookmarks"


def _per_page_table_text_overlap(md: str):
    """页内去重检查：表格单元格内容不得出现在同页文本块中。返回违规列表。"""
    norm = lambda s: re.sub(r"\s+", "", s or "")
    violations = []
    pages = re.split(r"<!-- Page (\d+) -->\n", md)
    for i in range(1, len(pages), 2):
        pnum, body = pages[i], pages[i + 1] if i + 1 < len(pages) else ""
        text_only = re.sub(r"<!-- Page \d+ Table \d+ -->\n(?:\|.*\n?)+", "", body)
        ntext = norm(text_only)
        for m in re.finditer(r"<!-- Page \d+ Table \d+ -->\n((?:\|.*\n?)+)", body):
            for cell in re.findall(r"\|([^|]+)\|", m.group(1)):
                frag = norm(cell.replace("<br>", ""))[:40]
                if len(frag) >= 20 and frag in ntext:
                    violations.append((pnum, frag[:30]))
    return violations


@pytest.mark.skipif(not CORPUS_DIR.exists(), reason="corpus data not available")
class TestV2CorpusIntegration:
    """真实语料端到端：数量基准 + 页内零重复 + 半边框表首列。"""

    @pytest.fixture(scope="class")
    def parser(self):
        return PDFParser(PDFConfig(strategy="local", local_version="v2"))

    @pytest.mark.parametrize("stem", sorted(CORPUS_EXPECT.keys()))
    def test_corpus_counts_and_no_duplication(self, parser, tmp_path, stem):
        md, meta = parser._convert_local_sync_v2(
            CORPUS_DIR / f"{stem}.pdf", storage=_StubStorage(tmp_path)
        )
        assert meta["tables_extracted"] == CORPUS_EXPECT[stem]["tables"]
        assert meta["images_extracted"] == CORPUS_EXPECT[stem]["images"]
        assert "\x01" not in md
        assert _per_page_table_text_overlap(md) == []

    def test_semi_bordered_table_keeps_first_column(self, parser, tmp_path):
        """飞书半边框表（左外框线缺失）首列不得丢为表外孤儿文本。"""
        md, _ = parser._convert_local_sync_v2(
            CORPUS_DIR / "CyClaw-2pages.pdf", storage=_StubStorage(tmp_path)
        )
        assert re.search(r"\|\s*端\s*\|\s*主要使用者\s*\|\s*核心价值\s*\|", md)

    def test_cross_page_table_merged_with_leaked_row(self, parser, tmp_path):
        """跨页续表：漏行回收 + 合并为一张表（1.3 核心概念，CyClaw-5pages P3→P4）。"""
        md, meta = parser._convert_local_sync_v2(
            CORPUS_DIR / "CyClaw-5pages.pdf", storage=_StubStorage(tmp_path)
        )
        assert meta["tables_extracted"] == 5  # 6 张检测表 - 1 处跨页合并
        # 漏行回收到表内（含单元格折行 "协作入"+"口" 的 CJK 合并）
        assert re.search(r"\|\s*频道 Channel\s*\|\s*将智能体接入⻜书、企业微信等外部协作入口\s*\|", md)
        # 合并后表头是 P3 的真表头，续表数据行不得成为表头
        concept_table = re.search(
            r"\| 概念\s*\| 说明\s*\| 使用位置\s*\|\n\| --- \| --- \| --- \|\n((?:\|.*\n?)+)", md
        )
        assert concept_table, "合并后的 1.3 核心概念表未找到"
        body = concept_table.group(1)
        assert "频道 Channel" in body and "可⻅范围" in body  # P3+P4 行在同一张表
        # 漏行不得再作为表外孤儿文本出现
        text_outside = re.sub(r"<!-- Page \d+ Table \d+ -->\n(?:\|.*\n?)+", "", md)
        assert "频道 Channel" not in text_outside
