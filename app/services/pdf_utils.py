"""
PDF Services - PyMuPDF-based PDF manipulation
Handles: Merge with letterhead, Combine PDFs, Optimize/Compress
"""
import fitz  # PyMuPDF
from PIL import Image
import io
from typing import List, BinaryIO

class PDFService:
    """PDF manipulation service using PyMuPDF"""
    
    @staticmethod
    async def merge_with_letterhead(
        content_bytes: bytes,
        letterhead_bytes: bytes,
        scale: float = 0.85
    ) -> bytes:
        """
        Merge content PDF with letterhead background
        
        Args:
            content_bytes: Content PDF file bytes
            letterhead_bytes: Letterhead PDF file bytes
            scale: Scale factor for content (0.85 = 85%)
            
        Returns:
            Merged PDF as bytes
        """
        # Open both PDFs
        content_doc = fitz.open(stream=content_bytes, filetype="pdf")
        letterhead_doc = fitz.open(stream=letterhead_bytes, filetype="pdf")
        
        # Create output PDF
        output_doc = fitz.open()
        
        # Get letterhead page (assume single page)
        letterhead_page = letterhead_doc[0]
       
        letterhead_width = letterhead_page.rect.width
        letterhead_height = letterhead_page.rect.height
        
        # Process each content page
        for page_num in range(len(content_doc)):
            # Create new page with letterhead size
            new_page = output_doc.new_page(
                width=letterhead_width,
                height=letterhead_height
            )
            
            # Add letterhead as background
            new_page.show_pdf_page(
                new_page.rect,
                letterhead_doc,
                0
            )
            
            # Scale and center content
            content_page = content_doc[page_num]
            content_rect = content_page.rect
            
            # Calculate scaled dimensions
            scaled_width = content_rect.width * scale
            scaled_height = content_rect.height * scale
            
            # Center on page
            x_offset = (letterhead_width - scaled_width) / 2
            y_offset = (letterhead_height - scaled_height) / 2
            
            # Define target rectangle (scaled and centered)
            target_rect = fitz.Rect(
                x_offset,
                y_offset,
                x_offset + scaled_width,
                y_offset + scaled_height
            )
            
            # Overlay content
            new_page.show_pdf_page(
                target_rect,
                content_doc,
                page_num
            )
            
            # Add page number in bottom right corner
            page_number_text = f"{page_num + 1} / {len(content_doc)}"
            text_point = fitz.Point(
                letterhead_width - 60,  # 60pt from right edge
                letterhead_height - 20   # 20pt from bottom
            )
            new_page.insert_text(
                text_point,
                page_number_text,
                fontsize=10,
                color=(0, 0, 0),  # Black color
                fontname="helv"   # Helvetica
            )
        
        # Save to bytes
        output_bytes = output_doc.tobytes(
            garbage=4,
            deflate=True
        )
        
        # Close documents
        content_doc.close()
        letterhead_doc.close()
        output_doc.close()
        
        return output_bytes
    
    @staticmethod
    async def combine_pdfs(pdf_files_bytes: List[bytes]) -> bytes:
        """
        Combine multiple PDFs into one
        
        Args:
            pdf_files_bytes: List of PDF file bytes
            
        Returns:
            Combined PDF as bytes
        """
        output_doc = fitz.open()
        
        for pdf_bytes in pdf_files_bytes:
            pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            output_doc.insert_pdf(pdf_doc)
            pdf_doc.close()
        
        output_bytes = output_doc.tobytes(
            garbage=4,
            deflate=True
        )
        
        output_doc.close()
        return output_bytes
    
    @staticmethod
    async def optimize_pdf(
        pdf_bytes: bytes,
        quality: int = 80
    ) -> bytes:
        """
        Optimize PDF by aggressively compressing and downscaling images
        
        Args:
            pdf_bytes: PDF file bytes
            quality: JPEG quality for images (60-95)
            
        Returns:
            Optimized PDF as bytes
        """
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        
        # Maximum dimension for images (downscale if larger)
        MAX_IMAGE_DIM = 1920
        
        # Process each page
        for page_num in range(len(doc)):
            page = doc[page_num]
            
            # Get all images on page
            image_list = page.get_images(full=True)
            
            for img in image_list:
                xref = img[0]
                
                try:
                    # Extract image
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    
                    # Convert to PIL Image
                    pil_image = Image.open(io.BytesIO(image_bytes))
                    
                    # Get original dimensions
                    original_width, original_height = pil_image.size
                    
                    # Downscale if too large
                    if max(original_width, original_height) > MAX_IMAGE_DIM:
                        if original_width > original_height:
                            new_width = MAX_IMAGE_DIM
                            new_height = int(original_height * (MAX_IMAGE_DIM / original_width))
                        else:
                            new_height = MAX_IMAGE_DIM
                            new_width = int(original_width * (MAX_IMAGE_DIM / original_height))
                        
                        pil_image = pil_image.resize((new_width, new_height), Image.Resampling.LANCZOS)
                    
                    # Convert to RGB if necessary
                    if pil_image.mode not in ("RGB", "L"):
                        if pil_image.mode == "RGBA":
                            # Create white background for transparent images
                            background = Image.new("RGB", pil_image.size, (255, 255, 255))
                            background.paste(pil_image, mask=pil_image.split()[3])  # Use alpha as mask
                            pil_image = background
                        else:
                            pil_image = pil_image.convert("RGB")
                    
                    # Compress aggressively
                    output = io.BytesIO()
                    pil_image.save(
                        output,
                        format="JPEG",
                        quality=quality,
                        optimize=True,
                        progressive=True
                    )
                    compressed_bytes = output.getvalue()
                    
                    # Only replace if smaller
                    if len(compressed_bytes) < len(image_bytes):
                        # Get image rectangle on page
                        img_rect = page.get_image_rects(xref)[0] if page.get_image_rects(xref) else None
                        
                        if img_rect:
                            # Remove old image
                            page.delete_image(xref)
                            
                            # Insert compressed image
                            page.insert_image(
                                img_rect,
                                stream=compressed_bytes
                            )
                
                except Exception as e:
                    # Skip problematic images
                    print(f"Could not compress image {xref}: {e}")
                    continue
        
        # Save with maximum compression
        output_bytes = doc.tobytes(
            garbage=4,            # Remove unused objects
            deflate=True,         # Compress streams
            clean=True,           # Clean structure
            deflate_images=True,  # Compress images
            deflate_fonts=True    # Compress fonts
        )
        
        doc.close()
        return output_bytes

