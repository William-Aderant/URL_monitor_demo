"""
Visual Diff Service

Generates visual comparisons between PDF versions with yellow highlighting
on changed areas.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple
import io

import structlog

logger = structlog.get_logger()


@dataclass
class DiffRegion:
    """A region that differs between two images."""
    x: int
    y: int
    width: int
    height: int
    page: int


@dataclass
class VisualDiffResult:
    """Result of visual diff generation."""
    success: bool
    diff_image_path: Optional[Path] = None
    changed_regions: List[DiffRegion] = None
    change_percentage: float = 0.0
    error: Optional[str] = None


class VisualDiff:
    """
    Generates visual diff images comparing two PDF versions.
    Changed areas are highlighted in yellow.
    """
    
    # Yellow highlight color (RGBA)
    HIGHLIGHT_COLOR = (255, 255, 0, 128)  # Semi-transparent yellow
    
    def __init__(self):
        """Initialize the visual diff generator."""
        logger.info("VisualDiff initialized")
    
    def render_pdf_page(self, pdf_path: Path, page_num: int = 0, dpi: int = 150) -> Optional[bytes]:
        """
        Render a PDF page to PNG image bytes.
        
        Args:
            pdf_path: Path to the PDF file
            page_num: Page number (0-indexed)
            dpi: Resolution for rendering
            
        Returns:
            PNG image bytes or None if failed
        """
        try:
            import fitz  # PyMuPDF
            
            doc = fitz.open(pdf_path)
            if page_num >= len(doc):
                logger.warning("Page number out of range", page=page_num, total=len(doc))
                return None
            
            page = doc[page_num]
            mat = fitz.Matrix(dpi/72, dpi/72)
            pix = page.get_pixmap(matrix=mat)
            
            img_bytes = pix.tobytes("png")
            doc.close()
            
            return img_bytes
            
        except Exception as e:
            logger.error("Failed to render PDF page", error=str(e))
            return None
    
    def compare_images(
        self, 
        img1_bytes: bytes, 
        img2_bytes: bytes,
        threshold: int = 30
    ) -> Tuple[bytes, List[DiffRegion], float]:
        """
        Compare two images and generate a diff image with yellow highlights.
        
        Args:
            img1_bytes: First image (old version) as PNG bytes
            img2_bytes: Second image (new version) as PNG bytes
            threshold: Pixel difference threshold (0-255)
            
        Returns:
            Tuple of (diff_image_bytes, changed_regions, change_percentage)
        """
        try:
            from PIL import Image, ImageDraw, ImageFilter
            import numpy as np
        except ImportError:
            logger.error("PIL/numpy not available for image comparison")
            raise ImportError("Pillow and numpy required for visual diff")
        
        # Load images
        img1 = Image.open(io.BytesIO(img1_bytes)).convert('RGB')
        img2 = Image.open(io.BytesIO(img2_bytes)).convert('RGB')
        
        # Resize to same dimensions if needed
        if img1.size != img2.size:
            # Use the larger dimensions
            max_width = max(img1.width, img2.width)
            max_height = max(img1.height, img2.height)
            
            img1 = img1.resize((max_width, max_height), Image.Resampling.LANCZOS)
            img2 = img2.resize((max_width, max_height), Image.Resampling.LANCZOS)
        
        # Convert to numpy arrays
        arr1 = np.array(img1, dtype=np.int16)
        arr2 = np.array(img2, dtype=np.int16)
        
        # Calculate absolute difference
        diff = np.abs(arr1 - arr2)
        
        # Create mask of changed pixels (any channel exceeds threshold)
        changed_mask = np.any(diff > threshold, axis=2)
        
        # Calculate change percentage
        total_pixels = changed_mask.size
        changed_pixels = np.sum(changed_mask)
        change_percentage = changed_pixels / total_pixels
        
        # Create output image (copy of new version)
        output = img2.copy().convert('RGBA')
        
        # Create yellow highlight overlay
        overlay = Image.new('RGBA', output.size, (0, 0, 0, 0))
        overlay_arr = np.array(overlay)
        
        # Apply yellow highlight to changed areas
        overlay_arr[changed_mask] = self.HIGHLIGHT_COLOR
        
        # Apply slight blur to smooth the highlights
        overlay = Image.fromarray(overlay_arr, 'RGBA')
        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=2))
        
        # Composite the highlight onto the image
        output = Image.alpha_composite(output, overlay)
        
        # Find bounding boxes of changed regions
        changed_regions = self._find_changed_regions(changed_mask)
        
        # Draw rectangles around significant changed regions
        if changed_regions:
            draw = ImageDraw.Draw(output)
            for region in changed_regions:
                draw.rectangle(
                    [region.x, region.y, region.x + region.width, region.y + region.height],
                    outline=(255, 200, 0, 255),  # Orange-yellow outline
                    width=2
                )
        
        # Convert back to PNG bytes
        output_buffer = io.BytesIO()
        output.save(output_buffer, format='PNG')
        output_buffer.seek(0)
        
        return output_buffer.read(), changed_regions, change_percentage
    
    def _find_changed_regions(
        self, 
        mask: 'np.ndarray', 
        min_area: int = 100
    ) -> List[DiffRegion]:
        """
        Find bounding boxes of changed regions in the mask.
        
        Args:
            mask: Boolean mask of changed pixels
            min_area: Minimum area for a region to be reported
            
        Returns:
            List of DiffRegion objects
        """
        try:
            import cv2
            import numpy as np
        except ImportError:
            # Fall back to simple approach without OpenCV
            return []
        
        # Convert mask to uint8 for OpenCV
        mask_uint8 = (mask.astype(np.uint8) * 255)
        
        # Apply morphological operations to clean up
        kernel = np.ones((5, 5), np.uint8)
        mask_uint8 = cv2.dilate(mask_uint8, kernel, iterations=2)
        mask_uint8 = cv2.erode(mask_uint8, kernel, iterations=1)
        
        # Find contours
        contours, _ = cv2.findContours(
            mask_uint8, 
            cv2.RETR_EXTERNAL, 
            cv2.CHAIN_APPROX_SIMPLE
        )
        
        regions = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = w * h
            
            if area >= min_area:
                regions.append(DiffRegion(
                    x=x, y=y, width=w, height=h, page=0
                ))
        
        # Sort by area (largest first)
        regions.sort(key=lambda r: r.width * r.height, reverse=True)
        
        return regions[:20]  # Limit to top 20 regions
    
    def generate_diff(
        self,
        old_pdf_path: Path,
        new_pdf_path: Path,
        output_path: Path,
        page_num: int = 0
    ) -> VisualDiffResult:
        """
        Generate a visual diff image comparing two PDF versions.
        
        Args:
            old_pdf_path: Path to the old PDF version
            new_pdf_path: Path to the new PDF version
            output_path: Path to save the diff image
            page_num: Page number to compare (0-indexed)
            
        Returns:
            VisualDiffResult with diff details
        """
        try:
            logger.info(
                "Generating visual diff",
                old=str(old_pdf_path),
                new=str(new_pdf_path),
                page=page_num
            )
            
            # Render both pages
            old_img = self.render_pdf_page(old_pdf_path, page_num)
            new_img = self.render_pdf_page(new_pdf_path, page_num)
            
            if not old_img:
                return VisualDiffResult(
                    success=False,
                    error="Failed to render old PDF"
                )
            
            if not new_img:
                return VisualDiffResult(
                    success=False,
                    error="Failed to render new PDF"
                )
            
            # Compare and generate diff
            diff_bytes, regions, change_pct = self.compare_images(old_img, new_img)
            
            # Save diff image
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'wb') as f:
                f.write(diff_bytes)
            
            logger.info(
                "Visual diff generated",
                output=str(output_path),
                change_percentage=f"{change_pct:.1%}",
                regions=len(regions)
            )
            
            return VisualDiffResult(
                success=True,
                diff_image_path=output_path,
                changed_regions=regions,
                change_percentage=change_pct
            )
            
        except Exception as e:
            logger.exception("Failed to generate visual diff", error=str(e))
            return VisualDiffResult(
                success=False,
                error=str(e)
            )
    
    def generate_side_by_side(
        self,
        old_pdf_path: Path,
        new_pdf_path: Path,
        output_path: Path,
        page_num: int = 0
    ) -> VisualDiffResult:
        """
        Generate a side-by-side comparison image with highlights.
        
        Args:
            old_pdf_path: Path to the old PDF version
            new_pdf_path: Path to the new PDF version
            output_path: Path to save the comparison image
            page_num: Page number to compare
            
        Returns:
            VisualDiffResult with comparison details
        """
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return VisualDiffResult(
                success=False,
                error="Pillow required for side-by-side comparison"
            )
        
        try:
            # Render both pages
            old_img_bytes = self.render_pdf_page(old_pdf_path, page_num)
            new_img_bytes = self.render_pdf_page(new_pdf_path, page_num)
            
            if not old_img_bytes or not new_img_bytes:
                return VisualDiffResult(
                    success=False,
                    error="Failed to render PDF pages"
                )
            
            # Generate diff overlay for the new image
            diff_bytes, regions, change_pct = self.compare_images(
                old_img_bytes, 
                new_img_bytes
            )
            
            # Load images
            old_img = Image.open(io.BytesIO(old_img_bytes)).convert('RGB')
            diff_img = Image.open(io.BytesIO(diff_bytes)).convert('RGB')
            
            # Create side-by-side image
            gap = 20
            header_height = 40
            total_width = old_img.width * 2 + gap
            total_height = old_img.height + header_height
            
            combined = Image.new('RGB', (total_width, total_height), (240, 240, 240))
            
            # Add headers
            draw = ImageDraw.Draw(combined)
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
            except:
                font = ImageFont.load_default()
            
            draw.text((old_img.width // 2 - 50, 10), "OLD VERSION", fill=(100, 100, 100), font=font)
            draw.text((old_img.width + gap + old_img.width // 2 - 80, 10), "NEW VERSION (changes highlighted)", fill=(100, 100, 100), font=font)
            
            # Paste images
            combined.paste(old_img, (0, header_height))
            combined.paste(diff_img, (old_img.width + gap, header_height))
            
            # Draw divider
            draw.line(
                [(old_img.width + gap // 2, header_height), 
                 (old_img.width + gap // 2, total_height)],
                fill=(200, 200, 200),
                width=2
            )
            
            # Save
            output_path.parent.mkdir(parents=True, exist_ok=True)
            combined.save(output_path, 'PNG')
            
            return VisualDiffResult(
                success=True,
                diff_image_path=output_path,
                changed_regions=regions,
                change_percentage=change_pct
            )
            
        except Exception as e:
            logger.exception("Failed to generate side-by-side", error=str(e))
            return VisualDiffResult(
                success=False,
                error=str(e)
            )
