"""
PDFix: Bulk PDF Optimizer.

Recursively finds every .pdf under a target directory and losslessly shrinks it via
PyMuPDF's garbage-collection + stream-deflate save flags (dead objects removed, streams
re-compressed). This is a structural optimization only - it does NOT rescale embedded
image resolution/DPI the way Paleographer's optimize_image() does for standalone images,
so gains on an already-tightly-scanned PDF may be modest.
"""

import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF
from dotenv import load_dotenv

# Global settings come from the project root's .env; this tool's own settings come from
# its own subfolder's .env, so PDFix stays runnable standalone.
# noinspection DuplicatedCode
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)


# ==========================================
# CONFIGURATION
# ==========================================
PROGRAM_DIR = os.getenv("PROGRAM_DIR", str(Path(__file__).resolve().parent.parent))
GENEALOGY_DIR = os.getenv("GENEALOGY_DIR", "")

_target = os.getenv("PDFIX_TARGET_DIR", "Media/Project")
TARGET_DIR = _target if os.path.isabs(_target) else os.path.join(GENEALOGY_DIR, _target)


def _get_env_int(key: str, default: int) -> int:
    val = os.getenv(key, "")
    try:
        return int(val.strip()) if val.strip() else default
    except ValueError:
        return default


def _get_env_float(key: str, default: float = 0.0) -> float:
    val = os.getenv(key, "")
    try:
        return float(val.strip()) if val.strip() else default
    except ValueError:
        return default


COMPRESSION_LEVEL = _get_env_int("PDFIX_COMPRESSION_LEVEL", 2)
CREATE_BACKUP = str(os.getenv("PDFIX_CREATE_BACKUP", "True")).lower() in ("true", "1", "yes")
REPAIR_MODE = str(os.getenv("PDFIX_REPAIR_MODE", "False")).lower() in ("true", "1", "yes")
_size_threshold = 0.0
SIZE_THRESHOLD_MB = _size_threshold if _size_threshold > 0 else None

# Module-level so Paleographer/engine.py can import it directly for the pre-send
# optimization step, rather than duplicating these params in two places.
COMPRESSION_PARAMS = {
    0: {"garbage": 1, "deflate": True, "clean": True},  # Low
    1: {"garbage": 3, "deflate": True, "clean": True},  # Medium
    2: {"garbage": 4, "deflate": True, "clean": True},  # High
}


# ==========================================
# CORE OPTIMIZATION
# ==========================================
def optimize_pdfs(directory, compression_level=1, backup=False, size_threshold_mb=None, repair_mode=False):
    """
    Optimize all PDFs in the given directory and its subdirectories.

    Args:
        directory: Directory to scan for PDFs
        compression_level: 0=low, 1=medium, 2=high compression
        backup: Whether to create backups of original files
        size_threshold_mb: Only optimize PDFs larger than this size (in MB)
        repair_mode: Whether to attempt repairs on damaged PDFs

    Returns:
        dict: Statistics about the optimization process
    """
    stats = {
        "total_files": 0,
        "optimized_files": 0,
        "skipped_files": 0,
        "failed_files": 0,
        "repaired_files": 0,
        "original_size_bytes": 0,
        "optimized_size_bytes": 0,
        "start_time": datetime.now(),
    }

    params = COMPRESSION_PARAMS.get(compression_level, COMPRESSION_PARAMS[1])
    processed_files = set()

    for root, _, files in os.walk(directory):
        for file in files:
            if not file.lower().endswith('.pdf'):
                continue

            pdf_path = os.path.join(root, file)

            # A file already optimized this run, or a leftover temp file from a prior
            # interrupted run, must not be re-processed.
            if pdf_path in processed_files or os.path.basename(pdf_path).startswith(".temp_opt_"):
                continue

            processed_files.add(pdf_path)
            stats["total_files"] += 1

            try:
                if not os.path.exists(pdf_path) or not os.access(pdf_path, os.R_OK):
                    print(f'Cannot access file: {pdf_path}')
                    stats["failed_files"] += 1
                    continue

                try:
                    file_size = os.path.getsize(pdf_path)
                    file_size_mb = file_size / (1024 * 1024)
                    stats["original_size_bytes"] += file_size
                except OSError as e:
                    print(f'Error getting size of {pdf_path}: {str(e)}')
                    stats["failed_files"] += 1
                    continue

                if size_threshold_mb and file_size_mb < size_threshold_mb:
                    print(f'Skipping {pdf_path} (size: {file_size_mb:.2f} MB, below threshold)')
                    stats["skipped_files"] += 1
                    stats["optimized_size_bytes"] += file_size  # No change for skipped files
                    continue

                try:
                    disk_usage = shutil.disk_usage(os.path.dirname(pdf_path))
                    if disk_usage.free < file_size * 2:  # optimize_pdf writes a full temp copy alongside the original
                        print(f'Skipping {pdf_path}: Not enough disk space')
                        stats["skipped_files"] += 1
                        stats["optimized_size_bytes"] += file_size
                        continue
                except Exception as e:
                    print(f'Warning: Could not check disk space for {pdf_path}: {str(e)}')

                if backup:
                    backup_path = pdf_path + '.backup'
                    try:
                        shutil.copy2(pdf_path, backup_path)
                    except Exception as e:
                        print(f'Warning: Could not create backup of {pdf_path}: {str(e)}')

                result = optimize_pdf(pdf_path, params, repair_mode)
                if result["success"]:
                    stats["optimized_files"] += 1
                    stats["optimized_size_bytes"] += result["new_size"]

                    original_size = float(result["original_size"])
                    new_size = float(result["new_size"])
                    reduction_percent = (
                        float((original_size - new_size) / original_size * 100)
                        if original_size > 0 else 0.0
                    )

                    print(f'Optimized: {pdf_path}')
                    print(
                        f'  Size: {original_size / 1024 / 1024:.2f} MB → '
                        f'{new_size / 1024 / 1024:.2f} MB ({reduction_percent:.1f}% reduction)')

                    if result.get("repaired", False):
                        stats["repaired_files"] += 1
                        print('  Note: Repaired PDF structure before optimization')
                else:
                    stats["failed_files"] += 1
                    stats["optimized_size_bytes"] += result["original_size"]  # No change in size for failed files
            except Exception as e:
                print(f'Unexpected error processing {pdf_path}: {str(e)}')
                stats["failed_files"] += 1
                try:
                    if os.path.exists(pdf_path):
                        file_size = os.path.getsize(pdf_path)
                        stats["optimized_size_bytes"] += file_size
                except OSError:
                    pass

    stats["end_time"] = datetime.now()
    stats["duration"] = stats["end_time"] - stats["start_time"]
    if stats["original_size_bytes"] > 0:
        stats["overall_reduction_percent"] = ((stats["original_size_bytes"] - stats["optimized_size_bytes"]) /
                                              stats["original_size_bytes"] * 100)
    else:
        stats["overall_reduction_percent"] = 0

    return stats


def optimize_pdf(pdf_path, params, repair_mode=False):
    """
    Optimize a single PDF file.

    Args:
        pdf_path: Path to the PDF file
        params: Optimization parameters
        repair_mode: Whether to attempt repair of damaged PDFs

    Returns:
        dict: Result of the optimization
    """
    original_size = os.path.getsize(pdf_path)
    result = {
        "success": False,
        "original_size": original_size,
        "new_size": original_size,
        "error": None,
        "repaired": False
    }

    temp_dir = os.path.dirname(pdf_path)
    temp_filename = f".temp_opt_{os.path.basename(pdf_path)}_{os.getpid()}_{int(time.time())}.pdf"
    temp_optimized_pdf_path = os.path.join(temp_dir, temp_filename)

    try:
        try:
            pdf_document = fitz.open(pdf_path)
        except Exception as e:
            if not repair_mode:
                raise e

            print(f'Attempting to repair damaged PDF: {pdf_path}')
            result["repaired"] = True
            pdf_document = page_by_page_recovery(pdf_path)
            if not isinstance(pdf_document, fitz.Document):
                raise Exception("PDF repair failed")

        if pdf_document.is_encrypted:
            print(f'Skipping encrypted PDF: {pdf_path}')
            pdf_document.close()
            result["error"] = "PDF is encrypted"
            return result

        if not pdf_document.can_save_incrementally():
            print(f'Warning: {pdf_path} may not support all optimizations')

        try:
            pdf_document.save(
                temp_optimized_pdf_path,
                incremental=False,
                garbage=params["garbage"],
                deflate=params["deflate"],
                clean=params["clean"]
            )
        except Exception as save_error:
            if not repair_mode:
                raise save_error

            # The standard save failed - retry with the least aggressive settings that
            # still compress at all, before falling back to page-by-page reconstruction.
            print(f'Using safe mode to optimize problematic PDF: {pdf_path}')
            # noinspection PyBroadException
            try:
                pdf_document.save(
                    temp_optimized_pdf_path,
                    incremental=True,
                    garbage=1,
                    deflate=True,
                    clean=False
                )
                result["repaired"] = True
            except Exception:
                print(f'Attempting page-by-page reconstruction for: {pdf_path}')
                pdf_document.close()
                if page_by_page_recovery(pdf_path, temp_optimized_pdf_path):
                    result["repaired"] = True
                else:
                    raise Exception("Could not repair PDF even with page-by-page method")

        # noinspection PyBroadException
        try:
            pdf_document.close()
        except Exception:
            pass

        if not os.path.exists(temp_optimized_pdf_path):
            raise Exception(f"Temporary optimized file was not created: {temp_optimized_pdf_path}")

        new_size = os.path.getsize(temp_optimized_pdf_path)

        if new_size < original_size:
            try:
                shutil.move(temp_optimized_pdf_path, pdf_path)
                result["success"] = True
                result["new_size"] = new_size
            except Exception as e:
                raise Exception(f"Failed to replace original file: {str(e)}")
        else:
            os.remove(temp_optimized_pdf_path)
            print(f'  No size reduction for {pdf_path}, keeping original')
            result["success"] = True  # Processing completed cleanly even though nothing changed

    except Exception as e:
        error_msg = str(e)
        print(f'Error optimizing {pdf_path}: {error_msg}')
        result["error"] = error_msg

        if "cannot find object in xref" in error_msg:
            print('  → This PDF has structural issues. Try using repair mode (-r flag)')
        elif "malformed or missing" in error_msg:
            print('  → This PDF may be damaged. Try using repair mode (-r flag)')

        try:
            if os.path.exists(temp_optimized_pdf_path):
                os.remove(temp_optimized_pdf_path)
        except Exception as cleanup_error:
            print(f'  Warning: Could not remove temporary file: {str(cleanup_error)}')

    return result


def page_by_page_recovery(pdf_path, output_path=None):
    """
    Attempt to repair a damaged PDF file.

    Args:
        pdf_path: Path to the PDF file
        output_path: If given, the repaired PDF is written here instead of overwriting
            pdf_path in place, and the return value only needs to be checked for
            truthiness. Used by optimize_pdf()'s page-by-page fallback, which needs a
            file to exist at this path afterward rather than a live fitz.Document.

    Returns:
        fitz.Document or None: when output_path is not given (repair succeeded or
        wasn't needed, or repair failed).
        bool: when output_path is given (True on success, None on failure).
    """
    try:
        pdf_document = fitz.open(pdf_path)
        if pdf_document.is_pdf and pdf_document.page_count > 0:
            if output_path is None:
                return pdf_document
            pdf_document.close()
            shutil.copy2(pdf_path, output_path)
            return True
        pdf_document.close()
    except Exception as e:
        print(f"Initial open failed: {e}")

    temp_file = None
    try:
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        temp_file.close()

        src_doc = None
        try:
            src_doc = fitz.open(pdf_path)
            new_doc = fitz.open()

            for page_num in range(src_doc.page_count):
                # noinspection PyBroadException
                try:
                    new_doc.insert_pdf(src_doc, from_page=page_num, to_page=page_num)
                except Exception:
                    print(f"Skipping problematic page {page_num}")
                    continue

            new_doc.save(temp_file.name)
            new_doc.close()

        except Exception as e:
            print(f"Page-by-page recovery failed: {e}")
            if src_doc:
                src_doc.close()
            raise
        finally:
            if src_doc:
                src_doc.close()

        repaired_doc = fitz.open(temp_file.name)
        if repaired_doc.page_count > 0:
            repaired_doc.close()
            if output_path is not None:
                shutil.copy2(temp_file.name, output_path)
                return True
            temp_repaired = pdf_path + '.repaired.pdf'
            shutil.copy2(temp_file.name, temp_repaired)
            os.replace(temp_repaired, pdf_path)
            return fitz.open(pdf_path)
        else:
            repaired_doc.close()

    except Exception as e:
        print(f"Recovery attempt failed: {e}")
    finally:
        if temp_file and os.path.exists(temp_file.name):
            try:
                os.unlink(temp_file.name)
            except OSError:
                pass

    return None


def print_summary(stats):
    """Print a summary of the optimization results."""
    print("\n" + "=" * 50)
    print("PDF OPTIMIZATION SUMMARY")
    print("=" * 50)
    print(f"Total PDFs processed: {stats['total_files']}")
    print(f"Successfully optimized: {stats['optimized_files']}")
    print(f"Repaired and optimized: {stats.get('repaired_files', 0)}")
    print(f"Skipped: {stats['skipped_files']}")
    print(f"Failed: {stats['failed_files']}")

    original_size_mb = float(stats["original_size_bytes"]) / (1024 * 1024)
    optimized_size_mb = float(stats["optimized_size_bytes"]) / (1024 * 1024)
    saved_mb = float(original_size_mb - optimized_size_mb)
    overall_reduction = float(stats["overall_reduction_percent"])

    print(f"\nOriginal size: {original_size_mb:.2f} MB")
    print(f"Optimized size: {optimized_size_mb:.2f} MB")
    print(f"Space saved: {saved_mb:.2f} MB ({overall_reduction:.1f}% reduction)")
    print(f"\nTime taken: {stats['duration']}")
    print("=" * 50)


# ==========================================
# MAIN EXECUTION
# ==========================================
def main() -> None:
    if not os.path.isdir(TARGET_DIR):
        print(f"Error: Target directory not found: {TARGET_DIR}")
        return

    print(f"\nStarting PDF optimization in {TARGET_DIR}...")
    stats = optimize_pdfs(
        TARGET_DIR,
        compression_level=COMPRESSION_LEVEL,
        backup=CREATE_BACKUP,
        size_threshold_mb=SIZE_THRESHOLD_MB,
        repair_mode=REPAIR_MODE
    )
    print_summary(stats)


if __name__ == "__main__":
    main()
