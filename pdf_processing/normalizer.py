"""
PDF normalization using qpdf and pikepdf.

This module provides deterministic PDF normalization to ensure
that byte-identical source PDFs produce identical normalized output
regardless of when they were downloaded.

Normalization steps:
1. qpdf linearization (structural normalization)
2. pikepdf metadata stripping (remove all variable metadata)
"""

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import pikepdf
import structlog

logger = structlog.get_logger()


@dataclass
class NormalizationResult:
    """Result of PDF normalization."""
    success: bool
    input_path: Path
    output_path: Optional[Path] = None
    original_size: Optional[int] = None
    normalized_size: Optional[int] = None
    error: Optional[str] = None
    qpdf_used: bool = False
    metadata_stripped: bool = False


class PDFNormalizer:
    """
    Normalizes PDFs for deterministic comparison.
    
    Uses qpdf for structural normalization and pikepdf for metadata stripping.
    """
    
    # Metadata keys to remove from PDF
    METADATA_KEYS_TO_REMOVE = [
        "/Producer",
        "/Creator", 
        "/CreationDate",
        "/ModDate",
        "/Author",
        "/Title",
        "/Subject",
        "/Keywords",
    ]
    
    def __init__(self, qpdf_path: str = "qpdf"):
        """
        Initialize normalizer.
        
        Args:
            qpdf_path: Path to qpdf executable
        """
        self.qpdf_path = qpdf_path
        self._verify_qpdf()
        logger.info("PDFNormalizer initialized", qpdf_path=qpdf_path)
    
    def _verify_qpdf(self) -> None:
        """Verify qpdf is available."""
        try:
            result = subprocess.run(
                [self.qpdf_path, "--version"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                version = result.stdout.strip().split('\n')[0]
                logger.info("qpdf found", version=version)
            else:
                logger.warning("qpdf not found or error", stderr=result.stderr)
        except FileNotFoundError:
            logger.warning(
                "qpdf not found - install with 'brew install qpdf' or 'apt install qpdf'"
            )
    
    def normalize(self, input_path: Path, output_path: Path) -> NormalizationResult:
        """
        Normalize a PDF file.
        
        Steps:
        1. Run qpdf to linearize and normalize structure
        2. Use pikepdf to strip all metadata
        3. Save final normalized PDF
        
        Args:
            input_path: Path to input PDF
            output_path: Path for normalized output
            
        Returns:
            NormalizationResult with normalization details
        """
        logger.info(
            "Starting PDF normalization",
            input=str(input_path),
            output=str(output_path)
        )
        
        if not input_path.exists():
            return NormalizationResult(
                success=False,
                input_path=input_path,
                error=f"Input file not found: {input_path}"
            )
        
        original_size = input_path.stat().st_size
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # Step 1: qpdf linearization
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                qpdf_output = Path(tmp.name)
            
            qpdf_success = self._run_qpdf(input_path, qpdf_output)
            
            if qpdf_success:
                intermediate_path = qpdf_output
            else:
                # If qpdf fails, continue with original
                logger.warning("qpdf failed, continuing with original PDF")
                intermediate_path = input_path
            
            # Step 2: pikepdf metadata stripping
            self._strip_metadata(intermediate_path, output_path)
            
            # Cleanup temp file
            if qpdf_success and qpdf_output.exists():
                qpdf_output.unlink()
            
            normalized_size = output_path.stat().st_size
            
            logger.info(
                "PDF normalized successfully",
                input=str(input_path),
                output=str(output_path),
                original_size=original_size,
                normalized_size=normalized_size
            )
            
            return NormalizationResult(
                success=True,
                input_path=input_path,
                output_path=output_path,
                original_size=original_size,
                normalized_size=normalized_size,
                qpdf_used=qpdf_success,
                metadata_stripped=True
            )
            
        except Exception as e:
            logger.error(
                "PDF normalization failed",
                input=str(input_path),
                error=str(e)
            )
            return NormalizationResult(
                success=False,
                input_path=input_path,
                error=str(e)
            )
    
    def _run_qpdf(self, input_path: Path, output_path: Path) -> bool:
        """
        Run qpdf to linearize and normalize PDF structure.
        
        Args:
            input_path: Input PDF path
            output_path: Output PDF path
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # qpdf options:
            # --linearize: Optimize for web viewing (consistent structure)
            # --normalize-content=y: Normalize content streams
            # --object-streams=disable: Disable object streams for consistency
            # --compress-streams=y: Compress streams consistently
            # --decode-level=generalized: Decode streams for comparison
            result = subprocess.run(
                [
                    self.qpdf_path,
                    "--linearize",
                    "--normalize-content=y",
                    "--object-streams=disable",
                    "--compress-streams=y",
                    str(input_path),
                    str(output_path)
                ],
                capture_output=True,
                text=True,
                timeout=120  # 2 minute timeout
            )
            
            if result.returncode == 0:
                logger.debug("qpdf normalization successful")
                return True
            elif result.returncode == 3:
                # qpdf returns 3 for warnings, file is still produced
                logger.debug("qpdf completed with warnings", warnings=result.stderr)
                return output_path.exists()
            else:
                logger.warning("qpdf failed", returncode=result.returncode, stderr=result.stderr)
                return False
                
        except subprocess.TimeoutExpired:
            logger.warning("qpdf timed out")
            return False
        except FileNotFoundError:
            logger.warning("qpdf not found")
            return False
        except Exception as e:
            logger.warning("qpdf error", error=str(e))
            return False
    
    def _strip_metadata(self, input_path: Path, output_path: Path) -> None:
        """
        Strip all metadata from PDF using pikepdf.
        
        Removes:
        - Document info dictionary entries
        - XMP metadata
        - Document ID
        
        Args:
            input_path: Input PDF path
            output_path: Output PDF path
        """
        logger.debug("Stripping PDF metadata", input=str(input_path))
        
        with pikepdf.open(input_path) as pdf:
            # Clear document info dictionary
            with pdf.open_metadata() as meta:
                # Delete all XMP metadata
                for key in list(meta.keys()):
                    try:
                        del meta[key]
                    except Exception:
                        pass
            
            # Remove info dictionary entries
            if pdf.docinfo:
                for key in self.METADATA_KEYS_TO_REMOVE:
                    if key in pdf.docinfo:
                        del pdf.docinfo[key]
                
                # Also remove any custom metadata
                keys_to_remove = [k for k in pdf.docinfo.keys() if k not in ["/Trapped"]]
                for key in keys_to_remove:
                    try:
                        del pdf.docinfo[key]
                    except Exception:
                        pass
            
            # Remove document ID (causes variation between downloads)
            if hasattr(pdf, 'trailer') and '/ID' in pdf.trailer:
                del pdf.trailer['/ID']
            
            # Save with deterministic settings
            pdf.save(
                output_path,
                linearize=False,  # Already linearized by qpdf
                deterministic_id=True  # Use deterministic ID
            )
        
        logger.debug("Metadata stripped successfully", output=str(output_path))
    
    def get_metadata(self, pdf_path: Path) -> dict:
        """
        Get metadata from a PDF file (for debugging/inspection).
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            Dictionary of metadata
        """
        metadata = {}
        
        try:
            with pikepdf.open(pdf_path) as pdf:
                # Document info
                if pdf.docinfo:
                    metadata["docinfo"] = {
                        str(k): str(v) for k, v in pdf.docinfo.items()
                    }
                
                # XMP metadata
                with pdf.open_metadata() as meta:
                    metadata["xmp"] = dict(meta)
                
                # Other properties
                metadata["page_count"] = len(pdf.pages)
                metadata["pdf_version"] = str(pdf.pdf_version)
                
        except Exception as e:
            metadata["error"] = str(e)
        
        return metadata


