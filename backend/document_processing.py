from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import warnings

from fastapi import HTTPException, UploadFile
from PIL import Image
from pypdf import PdfReader
from starlette.concurrency import run_in_threadpool

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_IMAGE_PIXELS = 20_000_000
MIN_PAGE_TEXT_CHARACTERS = 20
SUPPORTED_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


@dataclass
class ProcessedDocument:
    filename: str
    content_type: str
    type: str
    original_bytes: bytes
    text: str = ""
    requires_visual_processing: bool = False
    page_count: int | None = None
    width: int | None = None
    height: int | None = None

    def diagnostics(self) -> dict:
        # Keep document text and original bytes on the backend, out of the response.
        result = {
            "filename": self.filename,
            "content_type": self.content_type,
            "type": self.type,
            "size_bytes": len(self.original_bytes),
            "text_extracted": bool(self.text.strip()),
            "text_length": len(self.text),
            "requires_visual_processing": self.requires_visual_processing,
        }
        if self.page_count is not None:
            result["page_count"] = self.page_count
        if self.width is not None:
            result.update(width=self.width, height=self.height)
        return result


def extract_content(data: bytes, filename: str, content_type: str, field: str) -> ProcessedDocument:
    document = ProcessedDocument(filename, content_type, "pdf" if content_type == "application/pdf" else "image", data)
    try:
        if document.type == "pdf":
            if not data.startswith(b"%PDF-"):
                raise HTTPException(422, f"{field} is not a readable PDF.")
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise HTTPException(422, f"{field} is encrypted. Upload an unencrypted PDF.")
            document.page_count = len(reader.pages)
            if document.page_count == 0:
                raise HTTPException(422, f"{field} PDF contains no pages.")
            if document.page_count > MAX_PDF_PAGES:
                raise HTTPException(413, f"{field} PDF must contain at most {MAX_PDF_PAGES} pages.")
            page_texts = [(page.extract_text() or "").strip() for page in reader.pages]
            document.text = "\n\n".join(page_texts).strip()
            # Flag mixed PDFs too: a text cover page must not hide a scanned answer page.
            document.requires_visual_processing = any(
                len("".join(text.split())) < MIN_PAGE_TEXT_CHARACTERS for text in page_texts
            )
        else:
            expected_format = "PNG" if content_type == "image/png" else "JPEG"
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data), formats=["PNG", "JPEG"]) as image:
                    if image.format != expected_format:
                        raise HTTPException(422, f"{field} image content does not match its file type.")
                    document.width, document.height = image.size
                    if image.width * image.height > MAX_IMAGE_PIXELS:
                        raise HTTPException(413, f"{field} image must be at most 20 megapixels.")
                    image.verify()
                # Decoding catches truncated pixel data that header verification can miss.
                with Image.open(BytesIO(data), formats=["PNG", "JPEG"]) as image:
                    image.load()
            document.requires_visual_processing = True
        return document
    except HTTPException:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise HTTPException(413, f"{field} image dimensions are too large.") from error
    except Exception as error:
        # Parser errors vary by corrupt input; never expose internal exception details.
        raise HTTPException(422, f"{field} is corrupted or unreadable. Upload a valid PDF, PNG, or JPG/JPEG.") from error


async def process_document(file: UploadFile, field: str) -> ProcessedDocument:
    filename = file.filename or ""
    expected_type = SUPPORTED_TYPES.get(Path(filename).suffix.lower())
    if not expected_type or file.content_type not in (None, "", "application/octet-stream", expected_type):
        raise HTTPException(415, f"{field} must be a PDF, PNG, or JPG/JPEG file.")
    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, f"{field} must be 10 MB or smaller.")
    if not data:
        raise HTTPException(422, f"{field} file is empty.")
    return await run_in_threadpool(extract_content, data, filename, expected_type, field)
