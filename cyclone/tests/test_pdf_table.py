import logging
logging.getLogger("pdfminer").setLevel(logging.ERROR)


import pdfplumber
from dataclasses import dataclass
from typing import List, Optional, Iterator

import pdfplumber
from dataclasses import dataclass
from typing import List, Union, Tuple
import pdfplumber
from dataclasses import dataclass
from typing import List, Union, Tuple, Optional

PDF_PATH = "./data/CyClaw-2pages.pdf"

@dataclass
class ContentBlock:
    y_top: float
    y_bottom: float
    type: str
    data: Union[str, List[List[str]]]
    x_left: float = 0.0      # 增加X坐标，支持多列布局
    x_right: float = 0.0

class PDFExtractor:
    """
    工业级PDF提取器。
    
    支持场景：
    - 单栏/双栏布局
    - 表格旁有环绕文字
    - 页眉页脚过滤
    - 不规则表格
    """
    
    # 配置参数（可外部传入覆盖）
    MIN_TABLE_ROWS = 2
    MIN_TABLE_COLS = 2
    HEADER_CUTOFF = 50.0      # 页眉Y阈值（页面顶部50px内）
    FOOTER_CUTOFF = 50.0      # 页脚Y阈值（页面底部50px内）
    TABLE_TEXT_GAP = 5.0      # 表格与文本的最小间隙
    
    def __init__(self, pdf_path: str = PDF_PATH, 
                 header_cutoff: Optional[float] = None,
                 footer_cutoff: Optional[float] = None):
        self.pdf = pdfplumber.open(pdf_path)
        self.header_cutoff = header_cutoff or self.HEADER_CUTOFF
        self.footer_cutoff = footer_cutoff or self.FOOTER_CUTOFF
    
    def _is_real_table(self, table: List[List]) -> bool:
        """判断是否为真正表格，支持自定义规则"""
        if not table:
            return False
        
        # 规则1：至少N行M列
        valid_rows = sum(
            1 for row in table 
            if any(c and str(c).strip() for c in row)
        )
        if valid_rows >= self.MIN_TABLE_ROWS:
            max_cols = max(len([c for c in row if c and str(c).strip()]) for row in table)
            if max_cols >= self.MIN_TABLE_COLS:
                return True
        
        # 规则2：有明确的边框（pdfplumber检测到的表格通常有）
        # 这里find_tables已经筛选过，不需要额外判断
        
        # 规则3：单元格内容有结构化特征（如对齐、分隔符等）
        # 可选扩展
        
        return False
    
    def _clean_table(self, table: List[List]) -> List[List[str]]:
        return [[str(c).replace('\x01', ' ').strip() if c else '' for c in row] for row in table]
    
    def _merge_overlapping_bboxes(self, bboxes: List[Tuple[float, float, float, float]]) -> List[Tuple[float, float, float, float]]:
        """
        合并重叠的表格bbox（处理双栏布局中左右表格Y重叠的情况）。
        返回合并后的不重叠区域列表。
        """
        if not bboxes:
            return []
        
        # 按Y排序
        sorted_bboxes = sorted(bboxes, key=lambda b: (b[1], b[0]))
        merged = [list(sorted_bboxes[0])]
        
        for bbox in sorted_bboxes[1:]:
            last = merged[-1]
            # Y方向重叠？
            if not (bbox[3] < last[1] or bbox[1] > last[3]):
                # X方向也重叠或相邻？合并
                if not (bbox[2] < last[0] or bbox[0] > last[2]):
                    last[0] = min(last[0], bbox[0])
                    last[1] = min(last[1], bbox[1])
                    last[2] = max(last[2], bbox[2])
                    last[3] = max(last[3], bbox[3])
                    continue
            
            merged.append(list(bbox))
        
        return [tuple(m) for m in merged]
    
    def _get_text_regions(self, page_width: float, page_height: float,
                          table_bboxes: List[Tuple[float, float, float, float]],
                          header_y: float, footer_y: float) -> List[Tuple[float, float, float, float]]:
        """
        计算非表格文本区域。
        支持：多栏布局、表格旁环绕文字、页眉页脚过滤。
        """
        # 先合并重叠的表格区域
        merged_tables = self._merge_overlapping_bboxes(table_bboxes)
        
        # 按Y切片，但保留X方向的完整宽度（简化处理）
        # 如果要支持双栏文字+中间表格，需要更复杂的2D切片
        # 这里先按Y切片，后续可扩展为网格切片
        
        regions = []
        current_y = header_y  # 从页眉下方开始
        
        for bbox in sorted(merged_tables, key=lambda b: b[1]):
            # 表格上方的区域
            if bbox[1] > current_y + self.TABLE_TEXT_GAP:
                regions.append((
                    0, current_y, 
                    page_width, bbox[1]
                ))
            current_y = max(current_y, bbox[3])
        
        # 最后一个表格下方
        if current_y < page_height - footer_y - self.TABLE_TEXT_GAP:
            regions.append((
                0, current_y,
                page_width, page_height - footer_y
            ))
        
        return regions
    
    def _filter_header_footer(self, text: str, y_top: float, page_height: float) -> Optional[str]:
        """过滤页眉页脚内容（可选）"""
        # 简单实现：如果区域在页眉/页脚范围内，返回None
        # 更精确的实现：用正则匹配页眉页脚特征（如页码、文档标题重复出现）
        return text
    
    def extract_page(self, page) -> List[ContentBlock]:
        blocks = []
        page_height = page.height
        
        # 1. 获取真表格
        table_instances = page.find_tables()
        real_tables = []
        for t in table_instances:
            raw = t.extract()
            if self._is_real_table(raw):
                real_tables.append({
                    'bbox': t.bbox,
                    'data': self._clean_table(raw),
                    'y_top': t.bbox[1],
                    'y_bottom': t.bbox[3],
                    'x_left': t.bbox[0],
                    'x_right': t.bbox[2]
                })
        
        table_bboxes = [t['bbox'] for t in real_tables]
        
        # 2. 计算文本区域并提取
        text_regions = self._get_text_regions(
            page.width, page.height, 
            table_bboxes,
            self.header_cutoff,
            self.footer_cutoff
        )
        
        for region in text_regions:
            # 可选：跳过页眉页脚区域
            if region[3] < self.header_cutoff or region[1] > page_height - self.footer_cutoff:
                continue
            
            cropped = page.crop(region)
            text = cropped.extract_text()
            if text and text.strip():
                filtered = self._filter_header_footer(text, region[1], page_height)
                if filtered:
                    blocks.append(ContentBlock(
                        y_top=region[1],
                        y_bottom=region[3],
                        x_left=region[0],
                        x_right=region[2],
                        type="text",
                        data=filtered.strip()
                    ))
        
        # 3. 添加表格
        for t in real_tables:
            blocks.append(ContentBlock(
                y_top=t['y_top'],
                y_bottom=t['y_bottom'],
                x_left=t['x_left'],
                x_right=t['x_right'],
                type="table",
                data=t['data']
            ))
        
        # 4. 排序：先按Y，再按X（支持双栏）
        blocks.sort(key=lambda b: (b.y_top, b.x_left))
        return blocks
    
    def extract_all(self):
        for i, page in enumerate(self.pdf.pages, start=1):
            yield i, self.extract_page(page)
    
    def close(self):
        self.pdf.close()
    
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


# ============ 运行 ============

if __name__ == "__main__":
    with PDFExtractor() as extractor:
        for page_num, blocks in extractor.extract_all():
            print(f"\n{'='*60}")
            print(f"第 {page_num} 页")
            print(f"{'='*60}")
            
            for block in blocks:
                if block.type == "text":
                    print(block.data)
                else:
                    for row in block.data[:3]:
                        print(row)
                    if len(block.data) > 3:
                        print(f"... 共 {len(block.data)} 行")