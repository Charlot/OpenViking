# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
PDF parser for OpenViking.

Unified parser that converts PDF to Markdown then parses the result.
Supports dual strategy:
- Local: pdfplumber for direct conversion
- Remote: MinerU API for advanced conversion

This design simplifies PDF handling by delegating structure analysis
to the MarkdownParser after conversion.
"""

import asyncio
import io
import re
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from openviking.parse.base import (
    NodeType,
    ParseResult,
    ResourceNode,
    create_parse_result,
    lazy_import,
)
from openviking.parse.parsers.base_parser import BaseParser
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.parser_config import PDFConfig
import logging
logging.getLogger("pdfminer").setLevel(logging.ERROR)

logger = get_logger(__name__)


class PDFParser(BaseParser):
    """
    PDF parser with dual conversion strategy.

    Converts PDF → Markdown → ParseResult using MarkdownParser.
    When available, extracts PDF bookmarks/outlines and injects them as
    markdown headings so MarkdownParser can build a hierarchical directory
    structure instead of flat numbered files.

    Strategies:
    - "local": Use pdfplumber for text and table extraction
    - "mineru": Use MinerU API for advanced PDF processing
    - "auto": Try local first, fallback to MinerU if configured

    Examples:
        >>> # Local parsing
        >>> parser = PDFParser(PDFConfig(strategy="local"))
        >>> result = await parser.parse("document.pdf")

        >>> # Remote API parsing
        >>> config = PDFConfig(
        ...     strategy="mineru",
        ...     mineru_endpoint="https://api.example.com/convert",
        ...     mineru_api_key="key"
        ... )
        >>> parser = PDFParser(config)
        >>> result = await parser.parse("document.pdf")
    """

    def __init__(self, config: Optional[PDFConfig] = None):
        """
        Initialize PDF parser.

        Args:
            config: PDFConfig instance (defaults to auto strategy)
        """
        self.config = config or PDFConfig()
        self.config.validate()

        # Lazy import MarkdownParser to avoid circular imports
        self._markdown_parser = None

    def _get_markdown_parser(self):
        """Lazy import and create MarkdownParser."""
        if self._markdown_parser is None:
            from openviking.parse.parsers.markdown import MarkdownParser

            self._markdown_parser = MarkdownParser(config=self.config)
        return self._markdown_parser

    @property
    def supported_extensions(self) -> List[str]:
        """List of supported file extensions."""
        return [".pdf"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse PDF file.

        Args:
            source: Path to PDF file
            **kwargs: Additional options (resource_name/source_name for original filename)

        Returns:
            ParseResult with document tree

        Raises:
            FileNotFoundError: If PDF file doesn't exist
            ValueError: If conversion fails with all strategies
        """
        start_time = time.time()
        pdf_path = Path(source)

        # Get resource name from kwargs, prefer original filename from upload
        resource_name = kwargs.get("resource_name") or kwargs.get("source_name")

        if not pdf_path.exists():
            return create_parse_result(
                root=ResourceNode(type=NodeType.ROOT),
                source_path=str(pdf_path),
                source_format="pdf",
                parser_name="PDFParser",
                parse_time=time.time() - start_time,
                warnings=[f"File not found: {pdf_path}"],
            )

        try:
            # Step 1: Convert PDF to Markdown
            markdown_content, conversion_meta = await self._convert_to_markdown(
                pdf_path,
                resource_name=resource_name,
            )

            # Step 2: Parse Markdown using MarkdownParser, pass through resource name
            md_parser = self._get_markdown_parser()
            from openviking_cli.utils.storage import get_storage

            storage = get_storage()
            result = await md_parser.parse_content(
                markdown_content,
                source_path=str(pdf_path),
                resource_name=resource_name,
                source_name=resource_name,
                base_dir=pdf_path.parent,
                allowed_media_dirs=[storage.media_dir],
            )

            # Step 3: Update metadata for PDF origin
            result.source_format = "pdf"  # Override markdown format
            result.parser_name = "PDFParser"
            result.parser_version = "2.0"
            result.parse_time = time.time() - start_time
            result.meta.update(conversion_meta)
            result.meta["pdf_strategy"] = self.config.strategy
            result.meta["intermediate_markdown_length"] = len(markdown_content)
            result.meta["intermediate_markdown_preview"] = markdown_content[:500]

            logger.info(
                f"PDF parsed successfully: {pdf_path.name} "
                f"({len(markdown_content)} chars markdown, "
                f"{result.parse_time:.2f}s)"
            )

            return result

        except Exception as e:
            logger.error(f"Failed to parse PDF {pdf_path}: {e}")
            return create_parse_result(
                root=ResourceNode(type=NodeType.ROOT),
                source_path=str(pdf_path),
                source_format="pdf",
                parser_name="PDFParser",
                parse_time=time.time() - start_time,
                warnings=[f"Failed to parse PDF: {e}"],
            )

    async def _convert_to_markdown(
        self,
        pdf_path: Path,
        resource_name: Optional[str] = None,
    ) -> tuple[str, Dict[str, Any]]:
        """
        Convert PDF to Markdown using configured strategy.

        Args:
            pdf_path: Path to PDF file
            resource_name: Optional resource name for organizing saved images

        Returns:
            Tuple of (markdown_content, metadata_dict)

        Raises:
            ValueError: If all conversion strategies fail
        """
        if self.config.strategy == "local":
            return await self._convert_local(pdf_path, resource_name=resource_name)

        elif self.config.strategy == "mineru":
            return await self._convert_mineru(pdf_path, resource_name=resource_name)

        elif self.config.strategy == "auto":
            # Try local first
            try:
                return await self._convert_local(pdf_path, resource_name=resource_name)
            except Exception as e:
                logger.warning(f"Local conversion failed: {e}")

                # Fallback to MinerU if configured
                if self.config.mineru_endpoint:
                    logger.info("Falling back to MinerU API")
                    return await self._convert_mineru(pdf_path, resource_name=resource_name)
                else:
                    raise ValueError(
                        f"Local conversion failed and no MinerU endpoint configured: {e}"
                    )

        else:
            raise ValueError(f"Unknown strategy: {self.config.strategy}")

    async def _convert_local(
        self, pdf_path: Path, storage=None, resource_name: Optional[str] = None
    ) -> tuple[str, Dict[str, Any]]:
        # pdfplumber / pdfminer 的解析与图片/表格提取通常是 CPU/IO 密集且为同步实现，
        # 放到线程池中执行，避免阻塞事件循环。
        return await asyncio.to_thread(self._convert_local_sync, pdf_path, storage, resource_name)

    def _convert_local_sync(
        self, pdf_path: Path, storage=None, resource_name: Optional[str] = None
    ) -> tuple[str, Dict[str, Any]]:
        """同步版：用 pdfplumber 将 PDF 转 Markdown。

        该方法会在 :meth:`_convert_local` 中通过 asyncio.to_thread 调用。
        """
        if getattr(self.config, "local_version", "v2") == "v2":
            return self._convert_local_sync_v2(pdf_path, storage, resource_name)

        pdfplumber = lazy_import("pdfplumber")

        # Import storage utilities
        if storage is None:
            from openviking_cli.utils.storage import get_storage

            storage = get_storage()

        if resource_name is None:
            resource_name = pdf_path.stem

        parts = []
        meta = {
            "strategy": "local",
            "library": "pdfplumber",
            "pages_processed": 0,
            "images_extracted": 0,
            "tables_extracted": 0,
            "bookmarks_found": 0,
            "bookmarks_resolved": 0,
            "bookmarks_unresolved": 0,
            "headings_found": 0,
            "heading_source": "none",
        }

        try:
            with pdfplumber.open(str(pdf_path)) as pdf:
                meta["total_pages"] = len(pdf.pages)

                # Extract structure (bookmarks → font fallback), grouped by page
                bookmarks_by_page = self._collect_headings_by_page(pdf, meta)

                for page_num, page in enumerate(pdf.pages, 1):
                    try:
                        # Inject headings before page text
                        page_bookmarks = bookmarks_by_page.get(page_num, [])
                        for bm in page_bookmarks:
                            heading_prefix = "#" * bm["level"]
                            parts.append(f"\n{heading_prefix} {bm['title']}\n")

                        # Extract text
                        text = page.extract_text()
                        if text and text.strip():
                            # Add page marker as HTML comment
                            parts.append(f"<!-- Page {page_num} -->\n{text.strip()}")
                            meta["pages_processed"] += 1

                        # Extract tables
                        tables = page.extract_tables()
                        for table_idx, table in enumerate(tables or []):
                            if table and len(table) > 0:
                                md_table = self._format_table_markdown(table)
                                if md_table:
                                    parts.append(
                                        f"<!-- Page {page_num} Table {table_idx + 1} -->\n{md_table}"
                                    )
                                    meta["tables_extracted"] += 1

                        # Extract images
                        images = page.images
                        for img_idx, img in enumerate(images or []):
                            try:
                                # Extract image using underlying PDF object
                                image_obj = self._extract_image_from_page(page, img)
                                if image_obj:
                                    # Save image
                                    filename = f"page{page_num}_img{img_idx + 1}"
                                    image_path = storage.save_image(
                                        resource_name, image_obj, filename=filename
                                    )

                                    # Generate path relative to the media root.
                                    rel_path = image_path.relative_to(storage.media_dir)
                                    parts.append(
                                        f"<!-- Page {page_num} Image {img_idx + 1} -->\n"
                                        f"![Page {page_num} Image {img_idx + 1}]({rel_path})"
                                    )
                                    meta["images_extracted"] += 1
                            except Exception as img_err:
                                logger.warning(
                                    f"Failed to extract image {img_idx + 1} on page {page_num}: {img_err}"
                                )
                    finally:
                        self._release_page_cache(page)

            if not parts:
                logger.warning(f"No content extracted from {pdf_path}")
                return "", meta

            markdown_content = "\n\n".join(parts)

            # Clean encoding artifacts from Chromium/pdfcpu-generated PDFs:
            # \x01 (SOH) used as space, CJK Compatibility Ideographs (U+2F00 block)
            markdown_content = markdown_content.replace("\x01", " ")
            markdown_content = unicodedata.normalize("NFKC", markdown_content)

            logger.info(
                f"Local conversion: {meta['pages_processed']}/{meta['total_pages']} pages, "
                f"{meta['headings_found']} headings ({meta['heading_source']}, "
                f"bookmarks={meta['bookmarks_found']}, "
                f"resolved={meta['bookmarks_resolved']}), "
                f"{meta['images_extracted']} images, {meta['tables_extracted']} tables → "
                f"{len(markdown_content)} chars"
            )

            return markdown_content, meta

        except Exception as e:
            logger.error(f"pdfplumber conversion failed: {e}")
            raise

    # ------------------------------------------------------------------
    # V2 本地转换：严格表格检测 + 字符级排除 + Y 混排
    # ------------------------------------------------------------------
    V2_FILL_RECT_MAX_SIZE = 3.0  # pt：短边小于此值的填充矩形视为表格细线，否则视为背景框
    V2_FULL_PAGE_AREA_RATIO = 0.75  # 单列表格面积占页面超过此比例视为背景框误检
    V2_MIN_IMAGE_SIZE = 20.0  # pt：小于此尺寸的图片（图标/装饰）跳过
    V2_MAX_IMAGE_AREA_RATIO = 0.9  # 面积占比超过此值的图片视为页面背景，跳过
    # 文本行重建：行右端距页面文本右缘小于此值视为排满整行（两端对齐），下一行为段落续行。
    # 标点压缩/避头尾会让对齐行尾短 0.5–1 个字符，容差取约 1.3 字符宽
    V2_FULL_WIDTH_TOLERANCE = 14.0  # pt
    V2_BOX_DRAWING_CHARS = frozenset("─│┌┐└┘├┤┬┴┼━┃┏┓┗┛┣┫┳┻╋═║")
    V2_BULLET_MAP = {"•": "- ", "◦": "  - ", "▪": "- ", "‣": "- "}
    # 跨页续表：漏行回收与合并的几何约束（宁缺毋滥，不满足则保持原样）
    V2_RECOVER_EDGE_TOLERANCE = 2.0  # pt：竖边与列边界对齐容差
    V2_STITCH_COL_TOLERANCE = 5.0  # pt：续表列边界对齐容差
    V2_STITCH_BOTTOM_GAP = 20.0  # pt：前页表底距内容底超过此值视为自然结束，不合并
    V2_STITCH_TOP_GAP = 60.0  # pt：续表顶距内容顶超过此值视为新表，不合并

    def _convert_local_sync_v2(
        self, pdf_path: Path, storage=None, resource_name: Optional[str] = None
    ) -> tuple[str, Dict[str, Any]]:
        """V2 同步版：增强的 pdfplumber PDF → Markdown 转换。

        与 V1 的差异（针对飞书/Chromium 导出 PDF 的整页背景框导致的
        文本/表格重复问题）：
        1. 严格表格检测：真线段 + 描边矩形 + 细条填充矩形取双向边，
           大填充矩形（页面背景框）仅取竖边——横边会把整页闭合成假表格，
           竖边恰是半边框表格（左外框线缺失）首列的左边界；
        2. 整页级单列表格启发式过滤 + 提取结果丢弃全空列；
        3. 文本按字符级排除表格区域（page.filter），文本与表格零重叠、零丢失；
        4. 文本行 / 表格 / 图片按 Y 坐标混排，保留页内原始顺序；
        5. 图片过滤过小图标与整页背景。
        """
        pdfplumber = lazy_import("pdfplumber")

        if storage is None:
            from openviking_cli.utils.storage import get_storage

            storage = get_storage()

        if resource_name is None:
            resource_name = pdf_path.stem

        pages = []  # 逐页结构化块：先收集，跨页续表缝合后再渲染
        meta = {
            "strategy": "local",
            "local_version": "v2",
            "library": "pdfplumber",
            "pages_processed": 0,
            "images_extracted": 0,
            "tables_extracted": 0,
            "bookmarks_found": 0,
            "bookmarks_resolved": 0,
            "bookmarks_unresolved": 0,
            "headings_found": 0,
            "heading_source": "none",
        }

        try:
            with pdfplumber.open(str(pdf_path)) as pdf:
                meta["total_pages"] = len(pdf.pages)
                bookmarks_by_page = self._collect_headings_by_page(pdf, meta)

                for page_num, page in enumerate(pdf.pages, 1):
                    try:
                        headings = [
                            f"{'#' * bm['level']} {bm['title']}"
                            for bm in bookmarks_by_page.get(page_num, [])
                        ]

                        # (top, kind_order, kind, payload)
                        # kind_order：同一 Y 位置上 文本(0) < 表格(1) < 图片(2)
                        blocks = []

                        # --- 表格（严格检测 + 整页误检过滤 + 丢弃全空列） ---
                        # 保留结构化 rows/col_bounds，供跨页续表合并；渲染在缝合后
                        real_tables = self._find_real_tables(page)
                        table_bboxes = [t.bbox for t in real_tables]
                        table_dicts = []
                        for table in real_tables:
                            rows, keep = self._drop_empty_cols_with_idx(table.extract())
                            if not rows or not rows[0]:
                                continue
                            table_dicts.append(
                                {
                                    "rows": rows,
                                    "bbox": table.bbox,
                                    "col_bounds": self._table_col_bounds(table, keep),
                                    "top": table.bbox[1],
                                }
                            )
                        table_dicts.sort(key=lambda td: td["bbox"][1])
                        for td in table_dicts:
                            blocks.append((td["bbox"][1], 1, "table", td))

                        # --- 图片（过滤图标/背景，复用 V1 渲染与存储） ---
                        for img_idx, img in enumerate(page.images or []):
                            if not self._is_content_image(page, img):
                                continue
                            try:
                                image_obj = self._extract_image_from_page(page, img)
                                if image_obj:
                                    filename = f"page{page_num}_img{img_idx + 1}"
                                    image_path = storage.save_image(
                                        resource_name, image_obj, filename=filename
                                    )
                                    rel_path = image_path.relative_to(storage.media_dir)
                                    blocks.append(
                                        (
                                            img["top"],
                                            2,
                                            "image",
                                            f"<!-- Page {page_num} Image {img_idx + 1} -->\n"
                                            f"![Page {page_num} Image {img_idx + 1}]({rel_path})",
                                        )
                                    )
                                    meta["images_extracted"] += 1
                            except Exception as img_err:
                                logger.warning(
                                    f"Failed to extract image {img_idx + 1} "
                                    f"on page {page_num}: {img_err}"
                                )

                        # --- 文本（字符级排除表格区域，按行成块） ---
                        filtered = self._page_outside_tables(page, table_bboxes)
                        try:
                            text_lines = filtered.extract_text_lines()
                        except Exception:
                            text_lines = []
                        page_right_edge = 0.0
                        if text_lines:
                            page_right_edge = max(l["x1"] for l in text_lines)
                            # --- 漏行回收：跨页续表首行无顶边框检测不到，
                            # 若被列竖边向上延伸包围则按列聚回并 prepend 到首表 ---
                            consumed = set()
                            if table_dicts:
                                try:
                                    recovered, consumed, region_top = (
                                        self._recover_leaked_rows(
                                            getattr(filtered, "chars", []),
                                            text_lines,
                                            table_dicts[0],
                                            page,
                                        )
                                    )
                                    if recovered:
                                        td = table_dicts[0]
                                        td["rows"] = recovered + td["rows"]
                                        td["top"] = min(td["top"], region_top)
                                except Exception as rec_err:
                                    logger.debug(
                                        f"Row recovery skipped on page {page_num}: {rec_err}"
                                    )
                                    consumed = set()
                            for idx, line in enumerate(text_lines):
                                if idx in consumed:
                                    continue
                                if line["text"].strip():
                                    blocks.append((line["top"], 0, "text", line))
                        else:
                            # 兜底：extract_text_lines 不可用时整页提取
                            text = filtered.extract_text()
                            if text and text.strip():
                                for ln in text.splitlines():
                                    if ln.strip():
                                        blocks.append(
                                            (0.0, 0, "text", {"text": ln, "x1": 0.0})
                                        )

                        # --- 按 Y 混排，连续文本行重建为逻辑结构 ---
                        # （段落软换行合并 / 项目符号列表 / box-drawing 代码围栏）
                        page_parts = []
                        text_run = []
                        for _top, _order, kind, payload in sorted(
                            blocks, key=lambda b: (b[0], b[1])
                        ):
                            if kind == "text":
                                text_run.append(payload)
                                continue
                            if text_run:
                                page_parts.append(
                                    [
                                        "text",
                                        self._reconstruct_text_lines(
                                            text_run, page_right_edge
                                        ),
                                    ]
                                )
                                text_run = []
                            page_parts.append([kind, payload])
                        if text_run:
                            page_parts.append(
                                [
                                    "text",
                                    self._reconstruct_text_lines(text_run, page_right_edge),
                                ]
                            )

                        content_top, content_bottom = self._page_content_bounds(page)
                        pages.append(
                            {
                                "page_num": page_num,
                                "headings": headings,
                                "blocks": page_parts,
                                "content_top": content_top,
                                "content_bottom": content_bottom,
                            }
                        )
                    finally:
                        self._release_page_cache(page)

            # --- 跨页续表缝合（三重几何守卫，不满足保持原样） ---
            self._stitch_cross_page_tables(pages)

            # --- 渲染 ---
            parts = []
            for entry in pages:
                for heading in entry["headings"]:
                    parts.append(f"\n{heading}\n")
                if not entry["blocks"]:
                    continue
                rendered = []
                table_idx = 0
                for kind, payload in entry["blocks"]:
                    if kind == "table":
                        md_table = self._format_table_markdown(payload["rows"])
                        if not md_table:
                            continue
                        table_idx += 1
                        meta["tables_extracted"] += 1
                        rendered.append(
                            f"<!-- Page {entry['page_num']} Table {table_idx} -->\n{md_table}"
                        )
                    else:
                        rendered.append(payload)
                if rendered:
                    parts.append(
                        f"<!-- Page {entry['page_num']} -->\n" + "\n\n".join(rendered)
                    )
                    meta["pages_processed"] += 1

            if not parts:
                logger.warning(f"No content extracted from {pdf_path}")
                return "", meta

            markdown_content = "\n\n".join(parts)

            # Clean encoding artifacts from Chromium/pdfcpu-generated PDFs:
            # \x01 (SOH) used as space, CJK Compatibility Ideographs (U+2F00 block)
            markdown_content = markdown_content.replace("\x01", " ")
            markdown_content = unicodedata.normalize("NFKC", markdown_content)

            logger.info(
                f"Local conversion (v2): {meta['pages_processed']}/{meta['total_pages']} pages, "
                f"{meta['headings_found']} headings ({meta['heading_source']}, "
                f"bookmarks={meta['bookmarks_found']}, "
                f"resolved={meta['bookmarks_resolved']}), "
                f"{meta['images_extracted']} images, {meta['tables_extracted']} tables → "
                f"{len(markdown_content)} chars"
            )

            return markdown_content, meta

        except Exception as e:
            logger.error(f"pdfplumber v2 conversion failed: {e}")
            raise

    def _collect_headings_by_page(
        self, pdf: Any, meta: Dict[str, Any]
    ) -> Dict[int, List[Dict[str, Any]]]:
        """书签 → 字号 fallback 的标题检测，结果按页码分组（V1/V2 共用）。

        同时更新 meta 中的 bookmarks_found/resolved/unresolved、headings_found、
        heading_source 字段。
        """
        detection_mode = self.config.heading_detection
        bookmarks = []
        raw_bookmarks = []
        heading_source = "none"

        if detection_mode in ("bookmarks", "auto"):
            raw_bookmarks = self._extract_bookmarks(pdf)
            meta["bookmarks_found"] = len(raw_bookmarks)
            bookmarks = [bm for bm in raw_bookmarks if bm["page_num"] is not None]
            meta["bookmarks_resolved"] = len(bookmarks)
            meta["bookmarks_unresolved"] = len(raw_bookmarks) - len(bookmarks)

            if bookmarks:
                heading_source = "bookmarks"
            elif raw_bookmarks:
                logger.info(
                    "Bookmark detection found %d entries but none resolved to pages; "
                    "ignoring bookmark headings",
                    len(raw_bookmarks),
                )

        if not bookmarks and detection_mode in ("font", "auto"):
            bookmarks = self._detect_headings_by_font(pdf)
            if bookmarks:
                heading_source = "font_analysis"

        meta["headings_found"] = len(bookmarks)
        meta["heading_source"] = heading_source
        logger.info(
            "Heading detection: source=%s, headings=%d, bookmarks=%d, resolved=%d, "
            "unresolved=%d",
            heading_source,
            len(bookmarks),
            meta["bookmarks_found"],
            meta["bookmarks_resolved"],
            meta["bookmarks_unresolved"],
        )

        # Group bookmarks by page_num
        bookmarks_by_page = defaultdict(list)
        for bm in bookmarks:
            page = bm["page_num"]
            if page is None:
                continue
            bookmarks_by_page[page].append(bm)
        return bookmarks_by_page

    def _strict_table_settings(self, page: Any) -> Optional[Dict[str, Any]]:
        """构建表格检测参数：排除背景框横边，保留背景框竖边。

        边界对象分两类：
        - 双向边（横+竖）：page.lines（真正画出的线段）、描边矩形
          （stroke=True 或 linewidth>0）、细条填充矩形（短边 <
          V2_FILL_RECT_MAX_SIZE，例如行列分隔线）；
        - 仅竖边：大填充矩形（页面背景框）。其横边位于页眉/页脚位置，
          会把整页闭合成假表格，必须丢弃；但其竖边与正文左右边界重合，
          恰是半边框表格（飞书表格常缺左侧外框线）首列的左边界，需要保留。
        返回 None 表示某方向边界不足 2 条，不可能构成表格。
        """
        v_objs, h_objs = [], []
        for ln in page.lines:
            (v_objs if ln["top"] != ln["bottom"] else h_objs).append(ln)
        for r in page.rects:
            stroked = bool(r.get("stroke")) or (r.get("linewidth") or 0) > 0
            thin = min(r["width"], r["height"]) < self.V2_FILL_RECT_MAX_SIZE
            if stroked or thin:
                v_objs.append(r)
                h_objs.append(r)
            else:
                v_objs.append(r)  # 大背景框：仅竖边
        if len(v_objs) < 2 or len(h_objs) < 2:
            return None
        return {
            "vertical_strategy": "explicit",
            "explicit_vertical_lines": v_objs,
            "horizontal_strategy": "explicit",
            "explicit_horizontal_lines": h_objs,
        }

    def _find_real_tables(self, page: Any) -> List[Any]:
        """检测真实表格，并过滤"覆盖近乎整页的单列"误检。

        严格设置在个别 PDF 上异常时回退默认检测（启发式过滤仍然生效）。
        """
        settings = self._strict_table_settings(page)
        try:
            tables = page.find_tables(table_settings=settings) if settings else []
        except Exception as e:
            logger.debug(
                f"Strict table detection failed, fallback to default: {e}"
            )
            tables = page.find_tables()

        page_area = page.width * page.height
        real_tables = []
        for table in tables:
            x0, top, x1, bottom = table.bbox
            area_ratio = (x1 - x0) * (bottom - top) / page_area if page_area > 0 else 0
            if area_ratio > self.V2_FULL_PAGE_AREA_RATIO and len(table.columns) <= 1:
                continue  # 背景框误检：面积过大且只有 1 列
            real_tables.append(table)
        return real_tables

    @staticmethod
    def _drop_empty_cols(rows: List[List[Any]]) -> List[List[Any]]:
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

    @classmethod
    def _drop_empty_cols_with_idx(cls, rows: List[List[Any]]):
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

    @staticmethod
    def _table_col_bounds(table: Any, keep: List[int]) -> List[float]:
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

    @staticmethod
    def _page_content_bounds(page: Any):
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

    def _recover_leaked_rows(self, chars, text_lines, table_dict, page):
        """回收跨页续表漏出的首行（飞书续页首行无顶边框，检测不到表格闭合）。

        硬约束（宁缺毋滥）：候选文本行必须被 ≥2 条与表格列边界对齐、且从
        表体向上延伸的竖边（细填充竖条/真竖线）实际包围——普通正文段落
        周围没有竖边，不可能误回收。单列字符行（单元格折行）并入上一回收
        行的对应单元格。

        返回 (recovered_rows, consumed_line_idx, region_top)。
        """
        table_top = table_dict["bbox"][1]
        bounds = table_dict.get("col_bounds") or []
        if len(bounds) < 2:
            return [], set(), table_top

        # 与列边界对齐、从上方触及表顶的竖边
        v_edges = []  # (x, top, bottom)
        for ln in getattr(page, "lines", []) or []:
            if ln["top"] == ln["bottom"]:
                continue
            x = (ln["x0"] + ln["x1"]) / 2
            if ln["top"] < table_top - 1 and ln["bottom"] >= table_top - 1:
                if any(abs(x - b) <= self.V2_RECOVER_EDGE_TOLERANCE for b in bounds):
                    v_edges.append((x, ln["top"], ln["bottom"]))
        for r in getattr(page, "rects", []) or []:
            if (
                r["width"] >= self.V2_FILL_RECT_MAX_SIZE
                or r["height"] < self.V2_FILL_RECT_MAX_SIZE
            ):
                continue  # 只看细竖条
            x = (r["x0"] + r["x1"]) / 2
            if r["top"] < table_top - 1 and r["bottom"] >= table_top - 1:
                if any(abs(x - b) <= self.V2_RECOVER_EDGE_TOLERANCE for b in bounds):
                    v_edges.append((x, r["top"], r["bottom"]))
        if len({round(x, 1) for x, _, _ in v_edges}) < 2:
            return [], set(), table_top
        region_top = min(t for _, t, _ in v_edges)

        recovered = []
        consumed = set()
        for idx, line in enumerate(text_lines):
            if line["bottom"] > table_top + 1 or line["top"] < region_top - 2:
                continue
            # 包围约束：≥2 条竖边覆盖该行的 Y 范围
            covering = [
                e for e in v_edges if e[1] <= line["top"] + 2 and e[2] >= line["bottom"] - 2
            ]
            if len({round(x, 1) for x, _, _ in covering}) < 2:
                continue
            # 行内字符按列 x 聚回单元格
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
                recovered[-1][j] = self._join_two_lines(recovered[-1][j], texts[j])
                consumed.add(idx)
        return recovered, consumed, region_top

    @staticmethod
    def _norm_row(row: List[Any]) -> List[str]:
        return [re.sub(r"\s+", "", c or "") for c in row]

    def _stitch_cross_page_tables(self, pages: List[Dict[str, Any]]) -> None:
        """跨页续表合并：Page N 首块为表且 Page N-1 末块为表时，三重几何
        守卫同时满足才合并（任一不满足保持原样）：

        1. 列数相同且列边界 x 对齐（V2_STITCH_COL_TOLERANCE）；
        2. 前页表底贴近页面内容底——表是被页边界切断的，而非自然结束；
        3. 次页表顶贴近内容顶——表从页首开始（含漏行回收区）。

        续表首行若与前页表头重复（每页重复表头的 PDF）则丢弃。
        """
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
            if any(abs(a - b) > self.V2_STITCH_COL_TOLERANCE for a, b in zip(pb, tb)):
                continue
            # 守卫 2 应看表当前实际结束页的内容底（链式合并后结束页会前移）
            end_idx = p.get("end_idx", j)
            if pages[end_idx]["content_bottom"] - p["bbox"][3] > self.V2_STITCH_BOTTOM_GAP:
                continue
            if t["top"] - cur["content_top"] > self.V2_STITCH_TOP_GAP:
                continue
            rows = t["rows"]
            if rows and p["rows"] and self._norm_row(rows[0]) == self._norm_row(p["rows"][0]):
                rows = rows[1:]
            p["rows"].extend(rows)
            p["bbox"] = (p["bbox"][0], p["bbox"][1], p["bbox"][2], t["bbox"][3])
            p["end_idx"] = i
            cur["blocks"].pop(0)

    @staticmethod
    def _page_outside_tables(page: Any, bboxes: List[tuple]) -> Any:
        """返回剔除表格区域内字符后的页面，供文本提取使用（防重复）。"""

        def keep(obj):
            if obj["object_type"] != "char":
                return True
            cx = (obj["x0"] + obj["x1"]) / 2
            cy = (obj["top"] + obj["bottom"]) / 2
            return not any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in bboxes)

        return page.filter(keep)

    def _is_content_image(self, page: Any, img: Dict[str, Any]) -> bool:
        """判断是否为内容图片：跳过过小图标/装饰与整页背景图。"""
        x0 = max(0.0, img["x0"])
        top = max(0.0, img["top"])
        x1 = min(page.width, img["x1"])
        bottom = min(page.height, img["bottom"])
        if x1 - x0 < self.V2_MIN_IMAGE_SIZE or bottom - top < self.V2_MIN_IMAGE_SIZE:
            return False  # 图标/装饰
        if (x1 - x0) * (bottom - top) > page.width * page.height * (
            self.V2_MAX_IMAGE_AREA_RATIO
        ):
            return False  # 页面背景
        return True

    @staticmethod
    def _release_page_cache(page: Any) -> None:
        """Release pdfplumber/pdfminer per-page caches when available."""
        close = getattr(page, "close", None)
        if callable(close):
            try:
                close()
                return
            except Exception:
                pass

        flush_cache = getattr(page, "flush_cache", None)
        if callable(flush_cache):
            try:
                flush_cache()
            except Exception:
                pass

    def _extract_bookmarks(self, pdf) -> List[Dict[str, Any]]:
        """Extract bookmark structure from PDF outlines.

        Returns: [{level: int, title: str, page_num: int(1-based)}]
        """
        try:
            if not hasattr(pdf, "doc") or not hasattr(pdf.doc, "get_outlines"):
                return []

            outlines = list(pdf.doc.get_outlines())
            if not outlines:
                return []

            page_ref_to_num = self._build_page_number_map(pdf)

            bookmarks = []
            for level, title, dest, _action, _se in outlines:
                if not title or not title.strip():
                    continue

                page_num = None
                try:
                    if dest and len(dest) > 0:
                        page_num = self._resolve_bookmark_page(
                            dest[0], page_ref_to_num, len(pdf.pages)
                        )
                except Exception:
                    pass

                bookmarks.append(
                    {
                        "level": min(max(level, 1), 6),
                        "title": title.strip(),
                        "page_num": page_num,
                    }
                )

            return bookmarks

        except Exception as e:
            # pdfminer 对 catalog 中无 /Outlines 的文档抛出无消息的 PDFNoOutlines，
            # 属于"文档没有书签"的正常情况，不应告警。
            if type(e).__name__ == "PDFNoOutlines":
                logger.debug("PDF has no outlines; skipping bookmark extraction")
                return []
            logger.warning(f"Failed to extract bookmarks: {type(e).__name__}: {e}")
            return []

    def _build_page_number_map(self, pdf) -> Dict[int, int]:
        """Build a lookup from PDF page object ids to 1-based page numbers.

        pdfminer outlines and link annotations reference page objects by object id.
        In pdfplumber these ids are exposed as ``page.page_obj.pageid``; some mocks
        or alternate inputs may still expose ``objid``, so we keep both.
        """
        page_ref_to_num: Dict[int, int] = {}
        for page_num, page in enumerate(pdf.pages, 1):
            page_obj = getattr(page, "page_obj", None)
            if page_obj is None:
                continue

            for attr_name in ("pageid", "objid"):
                ref_id = getattr(page_obj, attr_name, None)
                if isinstance(ref_id, int):
                    page_ref_to_num.setdefault(ref_id, page_num)

        return page_ref_to_num

    def _resolve_bookmark_page(
        self, page_ref: Any, page_ref_to_num: Dict[int, int], total_pages: int
    ) -> Optional[int]:
        """Resolve a bookmark destination to a 1-based page number."""
        ref_id = getattr(page_ref, "objid", None)
        if isinstance(ref_id, int):
            return page_ref_to_num.get(ref_id)

        if isinstance(page_ref, int):
            # 0-based integer page index (common in many PDF producers)
            candidate = page_ref + 1
            if 1 <= candidate <= total_pages:
                return candidate
            return None

        if hasattr(page_ref, "resolve"):
            resolved = page_ref.resolve()
            for attr_name in ("pageid", "objid"):
                resolved_id = getattr(resolved, attr_name, None)
                if isinstance(resolved_id, int):
                    return page_ref_to_num.get(resolved_id)

        return None

    def _detect_headings_by_font(self, pdf) -> List[Dict[str, Any]]:
        """Detect headings by font size analysis.

        Returns: [{level: int, title: str, page_num: int(1-based)}]
        """
        try:
            # Step 1: Sample font size distribution (every 5th page)
            size_counter: Counter = Counter()
            sample_pages = pdf.pages[::5]
            for page in sample_pages:
                try:
                    for char in page.chars:
                        if char["text"].strip():
                            rounded = round(char["size"] * 2) / 2
                            size_counter[rounded] += 1
                finally:
                    self._release_page_cache(page)

            if not size_counter:
                return []

            # Step 2: Determine body font size and heading font sizes
            body_size = size_counter.most_common(1)[0][0]
            min_delta = self.config.font_heading_min_delta

            heading_sizes = sorted(
                [
                    s
                    for s, count in size_counter.items()
                    if s >= body_size + min_delta and count < size_counter[body_size] * 0.5
                ],
                reverse=True,
            )

            max_levels = self.config.max_heading_levels
            heading_sizes = heading_sizes[:max_levels]

            if not heading_sizes:
                logger.debug(f"Font analysis: body_size={body_size}pt, no heading sizes found")
                return []

            size_to_level = {s: i + 1 for i, s in enumerate(heading_sizes)}
            logger.debug(
                f"Font analysis: body_size={body_size}pt, "
                f"heading_sizes={heading_sizes}, size_to_level={size_to_level}"
            )

            # Step 3: Extract heading text page by page
            headings: List[Dict[str, Any]] = []

            def flush_line(chars_to_flush: list, page_num: int) -> None:
                if not chars_to_flush:
                    return
                title = "".join(c["text"] for c in chars_to_flush).strip()
                size = round(chars_to_flush[0]["size"] * 2) / 2

                if len(title) < 2:
                    return
                if len(title) > 100:
                    return
                if title.isdigit():
                    return
                if re.match(r"^[\d\s.·…]+$", title):
                    return

                headings.append(
                    {
                        "level": size_to_level[size],
                        "title": title,
                        "page_num": page_num,
                    }
                )

            for page in pdf.pages:
                try:
                    page_num = page.page_number + 1
                    chars = sorted(page.chars, key=lambda c: (c["top"], c["x0"]))

                    current_line_chars: list = []
                    current_top = None

                    for char in chars:
                        # Performance: headings won't appear in bottom 70% of page
                        if char["top"] > page.height * 0.3:
                            flush_line(current_line_chars, page_num)
                            current_line_chars = []
                            break

                        rounded_size = round(char["size"] * 2) / 2
                        if rounded_size not in size_to_level:
                            flush_line(current_line_chars, page_num)
                            current_line_chars = []
                            current_top = None
                            continue

                        # Same line check (top offset < 2pt)
                        if current_top is not None and abs(char["top"] - current_top) > 2:
                            flush_line(current_line_chars, page_num)
                            current_line_chars = []

                        current_line_chars.append(char)
                        current_top = char["top"]

                    flush_line(current_line_chars, page_num)
                finally:
                    self._release_page_cache(page)

            # Step 4: Deduplicate - filter headers appearing on >30% of pages
            title_page_count: Counter = Counter(h["title"] for h in headings)
            total_pages = len(pdf.pages)
            header_titles = {t for t, c in title_page_count.items() if c > total_pages * 0.3}
            headings = [h for h in headings if h["title"] not in header_titles]

            logger.debug(
                f"Font heading detection: {len(headings)} headings found "
                f"(filtered {len(header_titles)} header titles)"
            )
            return headings

        except Exception as e:
            logger.warning(f"Failed to detect headings by font: {e}")
            return []

    def _extract_image_from_page(self, page, img_info: dict) -> Optional[bytes]:
        """
        Extract a PDF image as valid PNG bytes.

        Renders the image's bounding box on the page to a raster PNG via
        pdfplumber's ``crop().to_image()`` instead of returning the raw decoded
        XObject stream (which is not a valid image file and cannot be opened).

        Args:
            page: pdfplumber page object
            img_info: Image metadata from page.images

        Returns:
            PNG-encoded image bytes or None if extraction fails
        """
        try:
            # pdfplumber coordinates: ``top`` is measured from the top of the page.
            bbox = (
                max(0, img_info["x0"]),
                max(0, img_info["top"]),
                min(page.width, img_info["x1"]),
                min(page.height, img_info["bottom"]),
            )

            # Skip degenerate / zero-area boxes that cannot be cropped.
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                return None

            cropped = page.crop(bbox)
            page_image = cropped.to_image(resolution=self.config.image_resolution)

            buffer = io.BytesIO()
            page_image.save(buffer, format="PNG")
            return buffer.getvalue()

        except Exception as e:
            logger.debug(f"Image extraction error: {e}")
            return None

    async def _convert_mineru(
        self,
        pdf_path: Path,
        resource_name: Optional[str] = None,
    ) -> tuple[str, Dict[str, Any]]:
        """
        Convert PDF to Markdown using MinerU API.

        Args:
            pdf_path: Path to PDF file
            resource_name: Optional resource name (unused in MinerU conversion)

        Returns:
            Tuple of (markdown_content, metadata)

        Raises:
            ImportError: If httpx not installed
            Exception: If API call fails
        """
        httpx = lazy_import("httpx")

        if not self.config.mineru_endpoint:
            raise ValueError("MinerU endpoint not configured")

        meta = {
            "strategy": "mineru",
            "endpoint": self.config.mineru_endpoint,
            "api_version": None,
        }

        try:
            async with httpx.AsyncClient(timeout=self.config.mineru_timeout) as client:
                # Prepare file upload
                with open(pdf_path, "rb") as f:
                    files = {"file": (pdf_path.name, f, "application/pdf")}

                    # Prepare headers
                    headers = {}
                    if self.config.mineru_api_key:
                        headers["Authorization"] = f"Bearer {self.config.mineru_api_key}"

                    # Prepare request params
                    params = self.config.mineru_params or {}

                    # Make API request
                    logger.info(f"Calling MinerU API: {self.config.mineru_endpoint}")
                    response = await client.post(
                        self.config.mineru_endpoint,
                        files=files,
                        headers=headers,
                        params=params,
                    )
                    response.raise_for_status()

                # Parse response
                result = response.json()
                markdown_content = result.get("markdown", "")

                # Extract metadata from response
                meta["api_version"] = result.get("version")
                meta["processing_time"] = result.get("processing_time")
                meta["total_pages"] = result.get("total_pages")

                if not markdown_content:
                    logger.warning(f"MinerU returned empty content for {pdf_path}")

                logger.info(
                    f"MinerU conversion: {meta.get('total_pages', '?')} pages → "
                    f"{len(markdown_content)} chars"
                )

                return markdown_content, meta

        except Exception as e:
            logger.error(f"MinerU API call failed: {e}")
            raise

    @staticmethod
    def _is_cjk_char(ch: str) -> bool:
        return unicodedata.east_asian_width(ch) in ("W", "F")

    @classmethod
    def _join_cell_lines(cls, text: str) -> str:
        """合并表格单元格内的换行（pdfplumber 对视觉换行的单元格返回 \\n）。

        Markdown 表格行必须是单行，裸换行会破坏表格。CJK 字符之间的换行
        多为排版软换行（常断在词中间，如 "使用技\\n能"），直接相连；
        其余边界补空格，避免拉丁词粘连。
        """
        out = ""
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            out = cls._join_two_lines(out, ln)
        return out

    @classmethod
    def _join_two_lines(cls, a: str, b: str) -> str:
        """按 CJK 规则拼接两行：两端都是宽字符直接相连，否则补空格。"""
        if a and not (cls._is_cjk_char(a[-1]) and cls._is_cjk_char(b[0])):
            return a + " " + b
        return a + b

    @classmethod
    def _normalize_list_line(cls, text: str):
        """PDF 项目符号 / 有序编号 → Markdown 列表行。返回 (is_list, text)。"""
        for bullet, md in cls.V2_BULLET_MAP.items():
            if text.startswith(bullet):
                return True, md + text[1:].strip()
        if re.match(r"^\d+\.\s", text):
            return True, text
        return False, text

    @classmethod
    def _starts_new_block(cls, text: str) -> bool:
        """是否强制另起逻辑行：列表项，或 "1.5 ⻚面导航关系" 类章节编号行
        （章节编号行不是列表，但也绝不能被并入上一段落）。"""
        return bool(re.match(r"^\d+(\.\d+)+\s", text))

    @classmethod
    def _reconstruct_text_lines(cls, lines: List[Dict[str, Any]], right_edge: float) -> str:
        """连续视觉行 → Markdown 逻辑结构。

        预览塌陷问题：extract_text_lines 给出的是 PDF 视觉行，若全部用 \\n
        拼接，Markdown 预览会把段落内单 \\n 折叠成空格，列表/元信息/ASCII
        图全部塌成一段。重建规则：

        1. 含 box-drawing 字符的连续行（ASCII 树/代码块，含相邻 "N " 行号行）
           → ``` 围栏；
        2. 项目符号（• ◦ ▪ ‣）与 "N." 有序项 → Markdown 列表，连续列表项
           单行相连；
        3. 段落软换行合并：上一视觉行排满整行（右端贴近 right_edge，两端
           对齐排版特征）时当前行是续行，按 CJK 规则并入——源文件不再断词，
           检索友好；逻辑行之间以空行分隔。
        """
        # \x01（Chromium 排版间隙占位符）先归一为空格，否则列表/章节编号
        # 等行首模式匹配会失效（\x01 不是 \s）
        norm = [
            dict(l, text=l["text"].replace("\x01", " ").strip())
            for l in lines
            if l["text"].strip()
        ]
        if not norm:
            return ""

        # 代码区域标记：含 box-drawing 的行 + 相邻 "N " 行号行；
        # 区域须含 ≥1 个 box-drawing 行且长度 ≥2，孤立的行号行不误伤
        raw_code = [
            bool(cls.V2_BOX_DRAWING_CHARS.intersection(l["text"]))
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
                has_box = has_box or bool(cls.V2_BOX_DRAWING_CHARS.intersection(norm[j]["text"]))
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
            is_list, text = cls._normalize_list_line(text)
            is_full = line.get("x1", 0.0) >= right_edge - cls.V2_FULL_WIDTH_TOLERANCE
            if (
                prev_full
                and not is_list
                and not cls._starts_new_block(text)
                and blocks
                and blocks[-1][0] in ("para", "list")
            ):
                # 段落/列表项续行：并入上一逻辑行最后一行
                head, sep, last = blocks[-1][1].rpartition("\n")
                blocks[-1][1] = head + sep + cls._join_two_lines(last, text)
            elif is_list and blocks and blocks[-1][0] == "list":
                blocks[-1][1] += "\n" + text
            else:
                blocks.append(["list" if is_list else "para", text])
            prev_full = is_full

        out = []
        for kind, text in blocks:
            out.append(f"```\n{text}\n```" if kind == "code" else text)
        return "\n\n".join(out)

    def _format_table_markdown(self, table: List[List[Optional[str]]]) -> str:
        """
        Convert table data to Markdown table format.

        Args:
            table: 2D array of table cells

        Returns:
            Markdown table string

        Examples:
            >>> table = [["Name", "Age"], ["Alice", "30"], ["Bob", "25"]]
            >>> print(parser._format_table_markdown(table))
            | Name | Age |
            | --- | --- |
            | Alice | 30 |
            | Bob | 25 |
        """
        if not table or not table[0]:
            return ""

        # Clean cells and handle None values
        def clean_cell(cell):
            if cell is None:
                return ""
            text = str(cell).strip().replace("|", "\\|")  # Escape pipe characters
            # 单元格内裸换行会破坏 Markdown 表格（行必须单行），按 CJK 规则合并
            return self._join_cell_lines(text) if "\n" in text else text

        lines = []

        # Header row
        header = table[0]
        header_cells = [clean_cell(cell) for cell in header]
        lines.append("| " + " | ".join(header_cells) + " |")

        # Separator row
        separator = ["---"] * len(header)
        lines.append("| " + " | ".join(separator) + " |")

        # Data rows
        for row in table[1:]:
            # Pad row to match header length
            padded_row = row + [None] * (len(header) - len(row))
            cells = [clean_cell(cell) for cell in padded_row[: len(header)]]
            lines.append("| " + " | ".join(cells) + " |")

        return "\n".join(lines)

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """
        Parse PDF content string.

        Note: This method is not recommended for PDFParser as it requires
        file path for conversion tools. Use parse() with file path instead.

        Args:
            content: PDF content (not supported)
            source_path: Optional source path
            **kwargs: Additional options

        Raises:
            NotImplementedError: PDFParser requires file path
        """
        raise NotImplementedError(
            "PDFParser does not support parsing content strings. "
            "Use parse() with a file path instead."
        )
