"""
PDF Services - PyMuPDF-based PDF manipulation & upload validation
Handles: Merge with letterhead, Combine PDFs, Optimize/Compress, and strict PDF validation
"""

import io
import os
from typing import List, Optional, Tuple
import fitz  # PyMuPDF
from PIL import Image
from fastapi import UploadFile, HTTPException


def get_max_upload_file_bytes() -> int:
    """Retorna o limite máximo por arquivo enviado em bytes (padrão: 25 MB)."""
    mb = float(os.getenv("MAX_UPLOAD_FILE_SIZE_MB", "25"))
    return int(max(0.0001, mb) * 1024 * 1024)


def get_max_total_upload_bytes() -> int:
    """Retorna o limite máximo total de payload de arquivos por requisição em bytes (padrão: 60 MB)."""
    mb = float(os.getenv("MAX_TOTAL_UPLOAD_SIZE_MB", "60"))
    return int(max(0.0001, mb) * 1024 * 1024)


def get_max_combine_files() -> int:
    """Retorna a quantidade máxima de arquivos permitida na combinação (padrão: 20)."""
    return max(2, int(os.getenv("MAX_COMBINE_FILES", "20")))


def get_max_pdf_pages() -> int:
    """Retorna o número máximo de páginas permitido por operação (padrão: 300)."""
    return max(1, int(os.getenv("MAX_PDF_PAGES", "300")))


def validate_pdf_bytes(
    data: bytes,
    label: str = "Arquivo PDF",
    max_pages: Optional[int] = None,
) -> int:
    """
    Valida assinatura mágica (%PDF-), integridade estrutural com PyMuPDF,
    ausência de criptografia por senha e limite de páginas.
    Retorna a quantidade de páginas do documento.
    """
    if not data:
        raise HTTPException(status_code=400, detail=f"{label}: o arquivo enviado está vazio.")

    if b"%PDF-" not in data[:1024]:
        raise HTTPException(
            status_code=400,
            detail=f"{label}: cabeçalho inválido. Apenas arquivos PDF reais são aceitos.",
        )

    doc = None
    try:
        try:
            doc = fitz.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"{label}: arquivo PDF corrompido ou inválido.",
            ) from exc

        if not getattr(doc, "is_pdf", True):
            raise HTTPException(
                status_code=400,
                detail=f"{label}: o documento não é um PDF válido.",
            )

        if doc.needs_pass:
            raise HTTPException(
                status_code=400,
                detail=f"{label}: o PDF está protegido por senha e não pode ser processado.",
            )

        page_count = len(doc)
        if page_count < 1:
            raise HTTPException(
                status_code=400,
                detail=f"{label}: o PDF não contém páginas.",
            )

        limit_pages = max_pages if max_pages is not None else get_max_pdf_pages()
        if page_count > limit_pages:
            raise HTTPException(
                status_code=400,
                detail=f"{label}: o PDF possui {page_count} páginas e excede o limite de {limit_pages} páginas.",
            )

        return page_count
    finally:
        if doc is not None:
            doc.close()


async def read_and_validate_pdf_upload(
    file: UploadFile,
    label: str = "Arquivo PDF",
    max_file_bytes: Optional[int] = None,
    max_pages: Optional[int] = None,
) -> Tuple[bytes, int]:
    """
    Lê um UploadFile em blocos limitados (prevenindo exaustão de memória)
    e valida que se trata de um documento PDF íntegro.
    Retorna (bytes_do_pdf, quantidade_de_paginas).
    """
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail=f"{label}: apenas arquivos com extensão .pdf são permitidos.",
        )

    limit_bytes = max_file_bytes if max_file_bytes is not None else get_max_upload_file_bytes()
    chunk_size = 256 * 1024
    buffer = bytearray()

    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > limit_bytes:
            limit_mb = limit_bytes / (1024 * 1024)
            raise HTTPException(
                status_code=413,
                detail=f"{label} ('{filename}'): tamanho excede o limite máximo permitido ({limit_mb:.1f} MB).",
            )

    pdf_bytes = bytes(buffer)
    pages = validate_pdf_bytes(pdf_bytes, label=f"{label} ('{filename}')", max_pages=max_pages)
    return pdf_bytes, pages


class PDFService:
    """PDF manipulation service using PyMuPDF"""

    @staticmethod
    async def merge_with_letterhead(
        content_bytes: bytes,
        letterhead_bytes: bytes,
        scale: float = 0.85,
    ) -> bytes:
        """
        Merge content PDF with letterhead background.
        """
        content_doc = None
        letterhead_doc = None
        output_doc = None
        try:
            content_doc = fitz.open(stream=content_bytes, filetype="pdf")
            letterhead_doc = fitz.open(stream=letterhead_bytes, filetype="pdf")
            output_doc = fitz.open()

            letterhead_page = letterhead_doc[0]
            letterhead_width = letterhead_page.rect.width
            letterhead_height = letterhead_page.rect.height

            for page_num in range(len(content_doc)):
                new_page = output_doc.new_page(
                    width=letterhead_width,
                    height=letterhead_height,
                )

                new_page.show_pdf_page(
                    new_page.rect,
                    letterhead_doc,
                    0,
                )

                content_page = content_doc[page_num]
                content_rect = content_page.rect

                scaled_width = content_rect.width * scale
                scaled_height = content_rect.height * scale

                x_offset = (letterhead_width - scaled_width) / 2
                y_offset = (letterhead_height - scaled_height) / 2

                target_rect = fitz.Rect(
                    x_offset,
                    y_offset,
                    x_offset + scaled_width,
                    y_offset + scaled_height,
                )

                new_page.show_pdf_page(
                    target_rect,
                    content_doc,
                    page_num,
                )

                page_number_text = f"{page_num + 1} / {len(content_doc)}"
                text_point = fitz.Point(
                    letterhead_width - 60,
                    letterhead_height - 20,
                )
                new_page.insert_text(
                    text_point,
                    page_number_text,
                    fontsize=10,
                    color=(0, 0, 0),
                    fontname="helv",
                )

            output_bytes = output_doc.tobytes(
                garbage=4,
                deflate=True,
            )
            return output_bytes
        finally:
            if content_doc is not None:
                content_doc.close()
            if letterhead_doc is not None:
                letterhead_doc.close()
            if output_doc is not None:
                output_doc.close()

    @staticmethod
    async def combine_pdfs(pdf_files_bytes: List[bytes]) -> bytes:
        """
        Combine multiple PDFs into one.
        """
        output_doc = None
        try:
            output_doc = fitz.open()

            for pdf_bytes in pdf_files_bytes:
                pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                try:
                    output_doc.insert_pdf(pdf_doc)
                finally:
                    pdf_doc.close()

            output_bytes = output_doc.tobytes(
                garbage=4,
                deflate=True,
            )
            return output_bytes
        finally:
            if output_doc is not None:
                output_doc.close()

    @staticmethod
    async def optimize_pdf(
        pdf_bytes: bytes,
        quality: int = 80,
    ) -> bytes:
        """
        Optimize PDF by aggressively compressing and downscaling images.
        """
        doc = None
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            MAX_IMAGE_DIM = 1920

            for page_num in range(len(doc)):
                page = doc[page_num]
                image_list = page.get_images(full=True)

                for img in image_list:
                    xref = img[0]

                    try:
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]

                        pil_image = Image.open(io.BytesIO(image_bytes))
                        original_width, original_height = pil_image.size

                        if max(original_width, original_height) > MAX_IMAGE_DIM:
                            if original_width > original_height:
                                new_width = MAX_IMAGE_DIM
                                new_height = int(original_height * (MAX_IMAGE_DIM / original_width))
                            else:
                                new_height = MAX_IMAGE_DIM
                                new_width = int(original_width * (MAX_IMAGE_DIM / original_height))

                            pil_image = pil_image.resize((new_width, new_height), Image.Resampling.LANCZOS)

                        if pil_image.mode not in ("RGB", "L"):
                            if pil_image.mode == "RGBA":
                                background = Image.new("RGB", pil_image.size, (255, 255, 255))
                                background.paste(pil_image, mask=pil_image.split()[3])
                                pil_image = background
                            else:
                                pil_image = pil_image.convert("RGB")

                        output = io.BytesIO()
                        pil_image.save(
                            output,
                            format="JPEG",
                            quality=quality,
                            optimize=True,
                            progressive=True,
                        )
                        compressed_bytes = output.getvalue()

                        if len(compressed_bytes) < len(image_bytes):
                            rects = page.get_image_rects(xref)
                            img_rect = rects[0] if rects else None

                            if img_rect:
                                page.delete_image(xref)
                                page.insert_image(
                                    img_rect,
                                    stream=compressed_bytes,
                                )

                    except Exception as e:
                        print(f"Could not compress image {xref}: {e}")
                        continue

            output_bytes = doc.tobytes(
                garbage=4,
                deflate=True,
                clean=True,
                deflate_images=True,
                deflate_fonts=True,
            )
            return output_bytes
        finally:
            if doc is not None:
                doc.close()
