from dataclasses import dataclass
from io import BytesIO
from math import ceil, isfinite
from pathlib import Path, PurePosixPath
import re
import warnings
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph
from fastapi import HTTPException, UploadFile
from PIL import Image
import pymupdf
from pypdf import PdfReader
from starlette.concurrency import run_in_threadpool

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_STUDENT_IMAGE_FILES = 10
MAX_STUDENT_IMAGES_BYTES = 30 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_DOCX_MEMBER_BYTES = 20 * 1024 * 1024
MAX_DOCX_ARCHIVE_MEMBERS = 1000
MAX_DOCX_TEXT_CHARACTERS = 500_000
MAX_DOCX_EMBEDDED_IMAGES = 20
MAX_DOCX_EMBEDDED_IMAGE_BYTES = 5 * 1024 * 1024
MAX_DOCX_EMBEDDED_IMAGES_BYTES = 20 * 1024 * 1024
MIN_DOCX_IMAGE_DIMENSION = 32
MIN_DOCX_IMAGE_PIXELS = 4_096
MAX_PDF_PAGES = 50
MAX_RENDERED_PDF_PAGES = 10
PDF_RENDER_DPI = 144
MAX_RENDERED_PAGE_DIMENSION = 3_000
MAX_RENDERED_PAGE_PIXELS = 8_000_000
MAX_RENDERED_PAGE_BYTES = 4 * 1024 * 1024
MAX_RENDERED_PDF_BYTES = 20 * 1024 * 1024
PDF_JPEG_QUALITY = 85
MAX_IMAGE_PIXELS = 20_000_000
MIN_PAGE_TEXT_CHARACTERS = 20
SUPPORTED_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def safe_filename(filename: str | None) -> str:
    """Keep display metadata only: no paths, control characters, or unbounded names."""
    name = Path((filename or "").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return (name or "upload")[:255]


def expected_upload_type(file: UploadFile, field: str) -> str:
    expected_type = SUPPORTED_TYPES.get(Path(safe_filename(file.filename)).suffix.lower())
    if not expected_type or file.content_type not in (None, "", "application/octet-stream", expected_type):
        raise HTTPException(415, f"{field} must be a PDF, DOCX, PNG, or JPG/JPEG file.")
    return expected_type


@dataclass(frozen=True)
class EmbeddedImage:
    filename: str
    content_type: str
    original_bytes: bytes
    width: int
    height: int


@dataclass(frozen=True)
class PDFPageContent:
    page_number: int
    text: str = ""
    rendered_image: EmbeddedImage | None = None


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
    embedded_images: tuple[EmbeddedImage, ...] = ()
    pdf_pages: tuple[PDFPageContent, ...] = ()

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
        if self.embedded_images:
            result["embedded_image_count"] = len(self.embedded_images)
        if self.pdf_pages:
            result["rendered_pdf_page_count"] = sum(page.rendered_image is not None for page in self.pdf_pages)
        return result


def useful_pdf_text(text: str) -> bool:
    return len("".join(text.split())) >= MIN_PAGE_TEXT_CHARACTERS


def render_sparse_pdf_pages(data: bytes, filename: str, page_numbers: list[int],
                            field: str) -> dict[int, EmbeddedImage]:
    if len(page_numbers) > MAX_RENDERED_PDF_PAGES:
        raise HTTPException(
            413, f"{field} PDF may contain at most {MAX_RENDERED_PDF_PAGES} pages requiring visual fallback."
        )
    rendered: dict[int, EmbeddedImage] = {}
    total_bytes = 0
    scale = PDF_RENDER_DPI / 72
    try:
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise HTTPException(422, f"{field} is encrypted. Upload an unencrypted PDF.")
            for page_number in page_numbers:
                page = pdf[page_number - 1]
                width = ceil(page.rect.width * scale)
                height = ceil(page.rect.height * scale)
                if (not isfinite(page.rect.width) or not isfinite(page.rect.height) or
                        width <= 0 or height <= 0 or width > MAX_RENDERED_PAGE_DIMENSION or
                        height > MAX_RENDERED_PAGE_DIMENSION or width * height > MAX_RENDERED_PAGE_PIXELS):
                    raise HTTPException(413, f"{field} PDF page {page_number} dimensions are too large to render safely.")
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                         colorspace=pymupdf.csRGB, alpha=False)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                output = BytesIO()
                image.save(output, format="JPEG", quality=PDF_JPEG_QUALITY, optimize=True)
                image_bytes = output.getvalue()
                if len(image_bytes) > MAX_RENDERED_PAGE_BYTES:
                    raise HTTPException(413, f"{field} PDF page {page_number} rendered image is too large.")
                total_bytes += len(image_bytes)
                if total_bytes > MAX_RENDERED_PDF_BYTES:
                    raise HTTPException(413, f"{field} PDF rendered pages must total 20 MB or smaller.")
                rendered[page_number] = EmbeddedImage(
                    filename=f"{Path(filename).stem}-page-{page_number}.jpg",
                    content_type="image/jpeg", original_bytes=image_bytes,
                    width=pixmap.width, height=pixmap.height,
                )
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(422, f"{field} PDF pages could not be rendered safely.") from error
    return rendered


def validate_docx_archive(data: bytes, field: str):
    try:
        with ZipFile(BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_DOCX_ARCHIVE_MEMBERS:
                raise HTTPException(413, f"{field} DOCX contains too many archive entries.")
            names: set[str] = set()
            total_size = 0
            for member in members:
                name = member.filename
                path = PurePosixPath(name)
                if (not name or "\\" in name or path.is_absolute() or ".." in path.parts or
                        name in names or (member.external_attr >> 16) & 0o170000 == 0o120000):
                    raise HTTPException(422, f"{field} is not a safe, valid DOCX file.")
                names.add(name)
                if member.flag_bits & 0x1:
                    raise HTTPException(422, f"{field} DOCX encryption is not supported.")
                if member.file_size > MAX_DOCX_MEMBER_BYTES:
                    raise HTTPException(413, f"{field} DOCX contains an archive entry that is too large.")
                total_size += member.file_size
                if total_size > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise HTTPException(413, f"{field} DOCX expanded content must be 50 MB or smaller.")
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise HTTPException(422, f"{field} is missing the required Word document structure.")
            # Stream every member to verify CRCs while enforcing actual output limits even if metadata is dishonest.
            actual_total = 0
            for member in members:
                if not member.is_dir():
                    actual_member = 0
                    with archive.open(member) as source:
                        while chunk := source.read(64 * 1024):
                            actual_member += len(chunk)
                            actual_total += len(chunk)
                            if actual_member > MAX_DOCX_MEMBER_BYTES:
                                raise HTTPException(413, f"{field} DOCX contains an archive entry that is too large.")
                            if actual_total > MAX_DOCX_UNCOMPRESSED_BYTES:
                                raise HTTPException(413, f"{field} DOCX expanded content must be 50 MB or smaller.")
    except HTTPException:
        raise
    except (BadZipFile, EOFError, NotImplementedError, OSError, RuntimeError, ValueError) as error:
        raise HTTPException(422, f"{field} is corrupted or is not a valid DOCX file.") from error


def docx_text(document, field: str) -> str:
    sections: list[str] = []
    total = 0
    table_number = 0
    for element in document.element.body.iterchildren():
        section = ""
        if isinstance(element, CT_P):
            paragraph = Paragraph(element, document)
            text = paragraph.text.strip()
            if text:
                style_name = (paragraph.style.name or "").casefold() if paragraph.style else ""
                label = "Heading" if style_name.startswith("heading") else \
                    "List Item" if style_name.startswith("list") else "Paragraph"
                section = f"[{label}]\n{text}"
        elif isinstance(element, CT_Tbl):
            table_number += 1
            table = Table(element, document)
            rows = []
            for index, row in enumerate(table.rows, start=1):
                cells = [" ".join(cell.text.split()) for cell in row.cells]
                rows.append(f"Row {index}: {' | '.join(cells)}")
            if rows:
                section = f"[Table {table_number}]\n" + "\n".join(rows)
        if section:
            total += len(section) + 2
            if total > MAX_DOCX_TEXT_CHARACTERS:
                raise HTTPException(413, f"{field} DOCX contains too much extracted text.")
            sections.append(section)
    return "\n\n".join(sections)


def docx_images(document, field: str) -> tuple[EmbeddedImage, ...]:
    relationship_ids: list[str] = []
    for element in document.element.body.iter():
        if element.tag == qn("a:blip"):
            relationship_id = element.get(qn("r:embed"))
            if relationship_id and relationship_id not in relationship_ids:
                relationship_ids.append(relationship_id)

    supported: list[EmbeddedImage] = []
    total_bytes = 0
    supported_count = 0
    for relationship_id in relationship_ids:
        part = document.part.related_parts.get(relationship_id)
        content_type = getattr(part, "content_type", "")
        if content_type not in {"image/png", "image/jpeg"}:
            continue
        supported_count += 1
        if supported_count > MAX_DOCX_EMBEDDED_IMAGES:
            raise HTTPException(413, f"{field} DOCX may contain at most {MAX_DOCX_EMBEDDED_IMAGES} PNG/JPEG images.")
        data = part.blob
        if len(data) > MAX_DOCX_EMBEDDED_IMAGE_BYTES:
            raise HTTPException(413, f"{field} DOCX contains an embedded image larger than 5 MB.")
        total_bytes += len(data)
        if total_bytes > MAX_DOCX_EMBEDDED_IMAGES_BYTES:
            raise HTTPException(413, f"{field} DOCX embedded images must total 20 MB or smaller.")
        expected_format = "PNG" if content_type == "image/png" else "JPEG"
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data), formats=["PNG", "JPEG"]) as image:
                    if image.format != expected_format:
                        raise ValueError("image type mismatch")
                    width, height = image.size
                    if width * height > MAX_IMAGE_PIXELS:
                        raise HTTPException(413, f"{field} DOCX embedded image must be at most 20 megapixels.")
                    image.verify()
                with Image.open(BytesIO(data), formats=["PNG", "JPEG"]) as image:
                    image.load()
        except HTTPException:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise HTTPException(413, f"{field} DOCX embedded image dimensions are too large.") from error
        except Exception as error:
            raise HTTPException(422, f"{field} DOCX contains a corrupted embedded image.") from error
        if width < MIN_DOCX_IMAGE_DIMENSION or height < MIN_DOCX_IMAGE_DIMENSION or width * height < MIN_DOCX_IMAGE_PIXELS:
            continue
        filename = safe_filename(Path(str(part.partname)).name)
        supported.append(EmbeddedImage(filename, content_type, data, width, height))
    return tuple(supported)


def extract_docx_content(data: bytes, filename: str, content_type: str, field: str) -> ProcessedDocument:
    validate_docx_archive(data, field)
    try:
        document = Document(BytesIO(data))
        text = docx_text(document, field)
        images = docx_images(document, field)
        if not text.strip() and not images:
            raise HTTPException(422, f"{field} DOCX contains no readable text, tables, or supported images.")
        return ProcessedDocument(filename, content_type, "docx", data, text=text, embedded_images=images)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(422, f"{field} is corrupted or is not a valid DOCX file.") from error


def extract_content(data: bytes, filename: str, content_type: str, field: str) -> ProcessedDocument:
    if content_type == SUPPORTED_TYPES[".docx"]:
        return extract_docx_content(data, filename, content_type, field)
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
            sparse_pages = [index for index, text in enumerate(page_texts, start=1)
                            if not useful_pdf_text(text)]
            rendered_pages = render_sparse_pdf_pages(data, filename, sparse_pages, field) if sparse_pages else {}
            document.pdf_pages = tuple(
                PDFPageContent(
                    page_number=index,
                    text=text if useful_pdf_text(text) else "",
                    rendered_image=rendered_pages.get(index),
                )
                for index, text in enumerate(page_texts, start=1)
            )
            document.text = "\n\n".join(
                f"[PDF Page {index} - extracted text]\n{text}"
                for index, text in enumerate(page_texts, start=1) if useful_pdf_text(text)
            ).strip()
            document.requires_visual_processing = bool(sparse_pages)
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
        raise HTTPException(422, f"{field} is corrupted or unreadable. Upload a valid PDF, DOCX, PNG, or JPG/JPEG.") from error


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
        raise HTTPException(422, "Upload one PDF, one DOCX, or 1 to 10 PNG/JPG images as student work.")

    content_types = [expected_upload_type(upload, "Student work") for upload in uploads]
    document_types = {"application/pdf", SUPPORTED_TYPES[".docx"]}
    single_document_count = sum(content_type in document_types for content_type in content_types)
    if single_document_count:
        if len(uploads) > 1:
            if all(content_type == "application/pdf" for content_type in content_types):
                detail = "Upload only one PDF; multiple PDFs are not supported."
            elif all(content_type == SUPPORTED_TYPES[".docx"] for content_type in content_types):
                detail = "Upload only one DOCX; multiple DOCX files are not supported."
            else:
                detail = "Do not mix PDF or DOCX documents with other files in one submission."
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
