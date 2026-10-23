"""
Document Ingestion & Reading Pipeline.
Extracts selectable text, words, and 2D visual layout coordinates from digital PDFs,
and automatically falls back to local OCR (RapidOCR) for scanned or image pages.
"""
from typing import List, Dict, Any, Optional, Union
import pymupdf


class TextBlock:
    """Represents a text element with spatial coordinates on a page."""
    def __init__(
        self,
        text: str,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        page_num: int = 1,
        confidence: float = 1.0,
        is_ocr: bool = False
    ):
        self.text = text.strip()
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1
        self.page_num = page_num
        self.confidence = confidence
        self.is_ocr = is_ocr

    @property
    def bbox(self):
        return (self.x0, self.y0, self.x1, self.y1)

    def __repr__(self):
        return f"TextBlock(p{self.page_num}, ({self.x0:.1f},{self.y0:.1f}): {self.text[:30]!r})"


class PageContent:
    """Represents extracted text, layout blocks, and 2D visual lines for a single page."""
    def __init__(self, page_num: int, width: float, height: float):
        self.page_num = page_num
        self.width = width
        self.height = height
        self.blocks: List[TextBlock] = []
        self.visual_lines: List[str] = []
        self.raw_text: str = ""
        self.char_count: int = 0
        self.is_ocr: bool = False


class DocumentContent:
    """Represents complete document content across all pages."""
    def __init__(self):
        self.pages: List[PageContent] = []
        self.full_text: str = ""
        self.is_scanned: bool = False
        self.page_count: int = 0
        self.metadata: Dict[str, Any] = {}

    @property
    def diagnostics(self) -> Dict[str, Any]:
        return {
            "page_count": self.page_count,
            "is_scanned": self.is_scanned,
            "chars_per_page": [p.char_count for p in self.pages],
            "ocr_pages": [p.page_num for p in self.pages if p.is_ocr],
        }


class DocumentReader:
    """Reads PDF files using PyMuPDF with seamless OCR fallback."""

    def __init__(self, min_char_threshold: int = 50, ocr_dpi: int = 200):
        self.min_char_threshold = min_char_threshold
        self.ocr_dpi = ocr_dpi
        self._ocr_engine = None

    def _get_ocr_engine(self):
        """Lazy load RapidOCR engine to save startup time and memory."""
        if self._ocr_engine is None:
            from rapidocr_onnxruntime import RapidOCR
            self._ocr_engine = RapidOCR()
        return self._ocr_engine

    def read_pdf(self, source: Union[str, bytes]) -> DocumentContent:
        """
        Parses a PDF from file path or raw bytes.
        Attempts embedded text extraction first; triggers OCR only when necessary.
        """
        doc_content = DocumentContent()

        if isinstance(source, bytes):
            doc = pymupdf.open(stream=source, filetype="pdf")
        else:
            doc = pymupdf.open(source)

        doc_content.page_count = len(doc)
        all_page_texts = []

        for page_idx, page in enumerate(doc):
            pno = page_idx + 1
            rect = page.rect
            page_content = PageContent(page_num=pno, width=rect.width, height=rect.height)

            # 1. Attempt digital text extraction
            text_blocks = page.get_text("blocks")
            digital_text = page.get_text("text").strip()

            usable_digital = (
                len(digital_text) >= self.min_char_threshold and
                any(c.isalnum() for c in digital_text)
            )

            if usable_digital:
                page_content.raw_text = digital_text
                page_content.char_count = len(digital_text)
                page_content.is_ocr = False
                for b in text_blocks:
                    if b[6] == 0:  # Text block
                        b_text = b[4].strip()
                        if b_text:
                            page_content.blocks.append(
                                TextBlock(
                                    text=b_text,
                                    x0=b[0],
                                    y0=b[1],
                                    x1=b[2],
                                    y1=b[3],
                                    page_num=pno,
                                    confidence=1.0,
                                    is_ocr=False
                                )
                            )

                # Build 2D visual lines from words
                words = page.get_text("words")
                lines_by_y = {}
                for w in sorted(words, key=lambda it: (it[1], it[0])):
                    y_approx = round(w[1] / 6.0) * 6
                    lines_by_y.setdefault(y_approx, []).append(w[4])
                page_content.visual_lines = [" ".join(wl) for y, wl in sorted(lines_by_y.items())]

            else:
                # 2. Trigger OCR Fallback for this page
                doc_content.is_scanned = True
                page_content.is_ocr = True
                ocr_blocks, ocr_vlines, ocr_text = self._run_ocr(page, pno)
                page_content.blocks = ocr_blocks
                page_content.visual_lines = ocr_vlines
                page_content.raw_text = ocr_text
                page_content.char_count = len(ocr_text)

            page_content.blocks.sort(key=lambda blk: (round(blk.y0 / 10) * 10, blk.x0))
            doc_content.pages.append(page_content)
            all_page_texts.append(f"--- Page {pno} ---\n" + page_content.raw_text)

        doc_content.full_text = "\n\n".join(all_page_texts)
        doc.close()
        return doc_content

    def _run_ocr(self, page: pymupdf.Page, page_num: int) -> tuple[List[TextBlock], List[str], str]:
        """Runs OCR on a rendered page pixmap and returns blocks, visual lines, and text."""
        ocr_engine = self._get_ocr_engine()
        pix = page.get_pixmap(dpi=self.ocr_dpi)
        img_bytes = pix.tobytes("png")

        ocr_results, _ = ocr_engine(img_bytes)
        blocks = []
        lines_by_y = {}
        raw_lines = []

        if ocr_results:
            scale_x = page.rect.width / pix.width
            scale_y = page.rect.height / pix.height

            for item in ocr_results:
                poly, text, conf = item
                if not text or not text.strip():
                    continue

                xs = [pt[0] for pt in poly]
                ys = [pt[1] for pt in poly]
                x0 = min(xs) * scale_x
                x1 = max(xs) * scale_x
                y0 = min(ys) * scale_y
                y1 = max(ys) * scale_y

                clean_txt = text.strip()
                blocks.append(
                    TextBlock(
                        text=clean_txt,
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                        page_num=page_num,
                        confidence=float(conf),
                        is_ocr=True
                    )
                )
                raw_lines.append(clean_txt)

                y_approx = round(y0 / 12.0) * 12
                lines_by_y.setdefault(y_approx, []).append((x0, clean_txt))

        # Reconstruct 2D visual lines sorted by x within each line
        visual_lines = []
        for y, row_items in sorted(lines_by_y.items()):
            row_sorted = sorted(row_items, key=lambda it: it[0])
            visual_lines.append(" ".join(it[1] for it in row_sorted))

        return blocks, visual_lines, "\n".join(raw_lines)
