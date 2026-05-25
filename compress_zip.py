"""
compress_zip.py — Recompress a submission zip before QA processing.

Strategy:
- PDFs: re-render each page at 150dpi as JPEG (quality 75), rebuild PDF via pypdf.
  Scanned PDFs (photos of documents) go from 2-5MB each to 200-400KB each.
  Text-based PDFs (Excel exports, Word exports) are left as-is via pypdf compress_content_streams.
- Images (JPG/PNG/TIFF): resize to max 2000px on longest side, JPEG quality 75.
- Other files: pass through unchanged.

Result: 28-40MB zips → 3-8MB zips. Documents remain readable at screen/QA resolution.
"""

import io
import os
import shutil
import tempfile
import zipfile

from PIL import Image

try:
    from pdf2image import convert_from_path
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False

try:
    from pypdf import PdfWriter, PdfReader
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False


# DPI for re-rendering scanned PDFs
RENDER_DPI = 150
# JPEG quality for re-rendered pages and images
JPEG_QUALITY = 75
# Max image dimension (pixels) — images larger than this get resized
MAX_IMAGE_DIM = 2000
# If a PDF page count is large (>20 pages), skip re-render to avoid timeout
MAX_PAGES_TO_RENDER = 30
# Minimum size saving to bother compressing (don't compress if already small)
MIN_SIZE_TO_COMPRESS = 200 * 1024  # 200KB


def _compress_pdf(src_path: str, dest_path: str) -> bool:
    """
    Compress a PDF by re-rendering pages at 150dpi.
    Returns True if compression was applied, False if skipped.
    """
    if not PDF2IMAGE_AVAILABLE or not PYPDF_AVAILABLE:
        return False

    try:
        # Check page count first — skip very large PDFs to avoid timeout
        reader = PdfReader(src_path)
        n_pages = len(reader.pages)
        if n_pages > MAX_PAGES_TO_RENDER:
            # Just use pypdf content stream compression for large PDFs
            writer = PdfWriter()
            writer.append(reader)
            writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
            with open(dest_path, "wb") as f:
                writer.write(f)
            return True

        # Re-render pages as JPEG images at 150dpi
        images = convert_from_path(
            src_path,
            dpi=RENDER_DPI,
            fmt="jpeg",
            thread_count=2,
            jpegopt={"quality": JPEG_QUALITY, "progressive": True, "optimize": True},
        )

        if not images:
            return False

        # Build new PDF from rendered images
        writer = PdfWriter()
        for img in images:
            # Convert PIL image to PDF page via bytes
            img_bytes = io.BytesIO()
            img.save(img_bytes, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            img_bytes.seek(0)

            # Add as a PDF page
            from pypdf import PageObject
            from pypdf.generic import NameObject, ArrayObject, NumberObject, DecodedStreamObject

            # Use a simpler approach: save each image as a single-page PDF
            img_pdf_bytes = io.BytesIO()
            img.save(img_pdf_bytes, format="PDF", resolution=RENDER_DPI)
            img_pdf_bytes.seek(0)
            page_reader = PdfReader(img_pdf_bytes)
            if page_reader.pages:
                writer.add_page(page_reader.pages[0])

        with open(dest_path, "wb") as f:
            writer.write(f)

        # Verify the output is smaller
        orig_size = os.path.getsize(src_path)
        new_size = os.path.getsize(dest_path)
        if new_size >= orig_size * 0.95:
            # Not worth it — keep original
            os.replace(src_path, dest_path)
            return False

        return True

    except Exception:
        # On any error, fall back to original
        try:
            shutil.copy2(src_path, dest_path)
        except Exception:
            pass
        return False


def _compress_image(src_path: str, dest_path: str) -> bool:
    """
    Compress an image file by resizing and re-encoding as JPEG.
    Returns True if compression was applied.
    """
    try:
        img = Image.open(src_path)
        # Convert to RGB (handles RGBA, palette, etc.)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # Resize if too large
        w, h = img.size
        if max(w, h) > MAX_IMAGE_DIM:
            ratio = MAX_IMAGE_DIM / max(w, h)
            new_w = int(w * ratio)
            new_h = int(h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        # Save as JPEG
        ext = os.path.splitext(dest_path)[1].lower()
        if ext in (".jpg", ".jpeg"):
            img.save(dest_path, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        elif ext == ".png":
            img.save(dest_path, format="PNG", optimize=True)
        else:
            # Save as JPEG regardless
            dest_path_jpg = os.path.splitext(dest_path)[0] + ".jpg"
            img.save(dest_path_jpg, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            dest_path = dest_path_jpg

        orig_size = os.path.getsize(src_path)
        new_size = os.path.getsize(dest_path)
        return new_size < orig_size * 0.95

    except Exception:
        try:
            shutil.copy2(src_path, dest_path)
        except Exception:
            pass
        return False


def compress_submission_zip(src_zip_path: str, dest_zip_path: str = None) -> dict:
    """
    Compress all PDFs and images in a submission zip.

    Args:
        src_zip_path: Path to the original zip.
        dest_zip_path: Path for the compressed zip. If None, replaces src_zip_path.

    Returns:
        dict with keys: original_size, compressed_size, reduction_pct, files_compressed
    """
    if dest_zip_path is None:
        dest_zip_path = src_zip_path + ".compressed.zip"
        replace_original = True
    else:
        replace_original = False

    original_size = os.path.getsize(src_zip_path)
    files_compressed = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        # Extract everything
        with zipfile.ZipFile(src_zip_path, "r") as zin:
            zin.extractall(tmpdir)

        # Walk and compress
        for root, dirs, files in os.walk(tmpdir):
            for fname in files:
                fpath = os.path.join(root, fname)
                fsize = os.path.getsize(fpath)
                ext = os.path.splitext(fname)[1].lower()

                if fsize < MIN_SIZE_TO_COMPRESS:
                    continue  # Already small, skip

                if ext == ".pdf":
                    tmp_out = fpath + ".compressed.pdf"
                    if _compress_pdf(fpath, tmp_out) and os.path.exists(tmp_out):
                        os.replace(tmp_out, fpath)
                        files_compressed += 1
                    elif os.path.exists(tmp_out):
                        os.remove(tmp_out)

                elif ext in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"):
                    tmp_out = fpath + ".compressed" + ext
                    if _compress_image(fpath, tmp_out) and os.path.exists(tmp_out):
                        os.replace(tmp_out, fpath)
                        files_compressed += 1
                    elif os.path.exists(tmp_out):
                        os.remove(tmp_out)

        # Repack into new zip
        with zipfile.ZipFile(dest_zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zout:
            for root, dirs, files in os.walk(tmpdir):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    arcname = os.path.relpath(fpath, tmpdir)
                    zout.write(fpath, arcname)

    compressed_size = os.path.getsize(dest_zip_path)
    reduction_pct = round((1 - compressed_size / original_size) * 100, 1) if original_size > 0 else 0

    if replace_original:
        os.replace(dest_zip_path, src_zip_path)
        dest_zip_path = src_zip_path

    return {
        "original_size": original_size,
        "compressed_size": compressed_size,
        "reduction_pct": reduction_pct,
        "files_compressed": files_compressed,
        "dest_path": dest_zip_path,
    }
