from pathlib import Path

def extract_text(path):
    suffix = Path(path).suffix.lower()
    if suffix == ".txt":
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    if suffix == ".pdf":
        import fitz
        doc = fitz.open(path)
        text = "\n".join(page.get_text("text") for page in doc).strip()
        if text:
            return text
        # Scanned PDFs may contain page images rather than a text layer.
        try:
            import pytesseract
            from PIL import Image
            import io
            pages = []
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                image = Image.open(io.BytesIO(pix.tobytes("png")))
                pages.append(pytesseract.image_to_string(image))
            return "\n".join(pages)
        except Exception as exc:
            raise RuntimeError("هذا PDF ممسوح ضوئيًا ويحتاج OCR. ثبّت Tesseract مع اللغات المناسبة على الخادم.") from exc
    if suffix == ".docx":
        from docx import Document
        doc = Document(path)
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text.strip() for cell in row.cells))
        return "\n".join(parts)
    if suffix == ".pptx":
        from pptx import Presentation
        prs = Presentation(path)
        return "\n".join(shape.text for slide in prs.slides for shape in slide.shapes if hasattr(shape, "text"))
    if suffix in {".xlsx", ".xlsm"}:
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=True, read_only=True)
        rows = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                rows.append(" | ".join("" if v is None else str(v) for v in row))
        return "\n".join(rows)
    if suffix == ".csv":
        import csv
        with open(path, newline="", encoding="utf-8-sig") as f:
            return "\n".join(" | ".join(row) for row in csv.reader(f))
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        try:
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(path))
        except Exception as e:
            raise RuntimeError("تعذر OCR. ثبّت Pillow وpytesseract ومحرك Tesseract على الخادم.") from e
    raise ValueError("صيغة الملف غير مدعومة.")
