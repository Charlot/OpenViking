import pdfplumber
from dataclasses import dataclass
from typing import List, Optional, Iterator
import logging
logging.getLogger("pdfminer").setLevel(logging.ERROR)


@dataclass
class PageContent:
    page_num: int
    text: Optional[str] = None
    tables: Optional[List[List[List[str]]]] = None

class PDFExtractor:
    """工业级 PDF 提取器，避免文本/表格重复"""
    
    # 真表格判定阈值
    MIN_TABLE_ROWS = 2
    MIN_TABLE_COLS = 2
    MIN_CELL_TEXT_LEN = 1  # 单元格非空
    
    def __init__(self, pdf_path: str):
        self.pdf = pdfplumber.open(pdf_path)
    
    def _is_real_table(self, table: List[List[Optional[str]]]) -> bool:
        """判断是否为真正的表格（过滤飞书等导出的伪表格）"""
        if not table or len(table) < self.MIN_TABLE_ROWS:
            return False
        
        # 统计有效行列
        valid_rows = 0
        max_cols = 0
        for row in table:
            clean_cells = [
                str(c).replace('\x01', ' ').strip() 
                for c in row if c is not None
            ]
            if any(len(c) >= self.MIN_CELL_TEXT_LEN for c in clean_cells):
                valid_rows += 1
            max_cols = max(max_cols, len([c for c in clean_cells if c]))
        
        return valid_rows >= self.MIN_TABLE_ROWS and max_cols >= self.MIN_TABLE_COLS
    
    def _extract_text_excluding_tables(self, page) -> Optional[str]:
        """提取非表格区域的文本"""
        tables = page.find_tables()
        if not tables:
            return page.extract_text()
        
        table_bboxes = [t.bbox for t in tables]
        
        # 提取单词并过滤
        words = page.extract_words(
            keep_blank_chars=True,
            x_tolerance=3,
            y_tolerance=3
        )
        
        non_table_words = []
        for w in words:
            x, y = w['x0'], w['top']
            in_table = any(
                bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]
                for bbox in table_bboxes
            )
            if not in_table:
                non_table_words.append(w)
        
        # 按行重组文本
        if not non_table_words:
            return None
            
        lines = {}
        for w in non_table_words:
            y_key = round(w['top'], 1)
            lines.setdefault(y_key, []).append(w['text'])
        
        return '\n'.join(
            ' '.join(lines[y]) for y in sorted(lines.keys())
        )
    
    def extract_page(self, page, page_num: int) -> PageContent:
        """单页提取：表格优先，文本补充"""
        
        # 1. 提取并过滤表格
        raw_tables = page.extract_tables()
        real_tables = [t for t in raw_tables if self._is_real_table(t)]
        
        # 2. 如果页面有真表格，提取非表格区域的文本
        if real_tables:
            text = self._extract_text_excluding_tables(page)
            return PageContent(
                page_num=page_num,
                text=text,
                tables=real_tables
            )
        
        # 3. 无表格页面，直接提取全文
        return PageContent(
            page_num=page_num,
            text=page.extract_text(),
            tables=None
        )
    
    def extract_all(self) -> Iterator[PageContent]:
        """遍历所有页面"""
        for i, page in enumerate(self.pdf.pages, start=1):
            yield self.extract_page(page, i)
    
    def close(self):
        self.pdf.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()


# ============ 使用 ============

PDF_PATH = "./data/CyClaw-2pages.pdf"

with PDFExtractor(PDF_PATH) as extractor:
    for content in extractor.extract_all():
        print(f"\n{'='*50}")
        print(f"第 {content.page_num} 页")
        print(f"{'='*50}")
        
        if content.tables:
            # print(f"\n[发现 {len(content.tables)} 个表格]")
            for idx, table in enumerate(content.tables, 1):
                # print(f"\n--- 表格 {idx} ---")
                for row in table:
                    clean = [str(c).replace('\x01', ' ').strip() if c else '' for c in row]
                    print(clean)
        
        if content.text:
            # print(f"\n--- 文本内容 ---")
            print(content.text.strip())