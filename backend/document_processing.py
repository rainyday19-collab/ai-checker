from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
import warnings

from fastapi import HTTPException, UploadFile
from PIL import Image
from pypdf import PdfReader
from starlette.concurrency import run_in_threadpool

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_STUDENT_IMAGE_FILES = 10
MAX_STUDENT_IMAGES_BYTES = 30 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_IMAGE_PIXELS = 20_000_000
MIN_PAGE_TEXT_CHARACTERS = 20
SUPPORTED_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def safe_filename(filename: str | None) -> str:
    """Keep display metadata only: no paths, control characters, or unbounded names."""
    name = Path((filename or "").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return (name or "upload")[:255]


def expected_upload_type(file: UploadFile, field: str) -> str:
    expected_type = SUPPORTED_TYPES.get(Path(safe_filename(file.filename)).suffix.lower())
    if not expected_type or file.content_type not in (None, "", "application/octet-stream", expected_type):
        raise HTTPException(415, f"{field} must be a PDF, PNG, or JPG/JPEG file.")
    return expected_type


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
    filename = safe_filename(file.filename)
    expected_type = expected_upload_type(file, field)
    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, f"{field} must be 10 MB or smaller.")
    if not data:
        raise HTTPException(422, f"{field} file is empty.")
    return await run_in_threadpool(extract_content, data, filename, expected_type, field)


async def process_student_work_files(files: list[UploadFile] | None) -> list[ProcessedDocument]:
    """Validate one PDF or an ordered collection of image pages, then decode each file."""
    uploads = list(files or [])
    if not uploads:
        raise HTTPException(422, "Upload one PDF or 1 to 10 PNG/JPG images as student work.")

    content_types = [expected_upload_type(upload, "Student work") for upload in uploads]
    pdf_count = content_types.count("application/pdf")
    if pdf_count:
        if len(uploads) > 1:
            detail = "Upload only one PDF; multiple PDFs are not supported." if pdf_count == len(uploads) else \
                "Do not mix a PDF with image pages in one submission."
            raise HTTPException(422, detail)
    if len(uploads) > MAX_STUDENT_IMAGE_FILES:
        raise HTTPException(413, f"Student work may contain at most {MAX_STUDENT_IMAGE_FILES} image pages.")

    documents: list[ProcessedDocument] = []
    combined_bytes = 0
    for index, upload in enumerate(uploads, start=1):
        document = await process_document(upload, f"Student work page {index}" if len(uploads) > 1 else "Student work")
        documents.append(document)
        if document.type == "image":
            combined_bytes += len(document.original_bytes)
            if combined_bytes > MAX_STUDENT_IMAGES_BYTES:
                raise HTTPException(413, "Student work image pages must total 30 MB or smaller.")
    return documents
