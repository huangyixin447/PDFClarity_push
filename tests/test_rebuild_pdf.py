"""Exercise the real PDF extraction and rebuild commands on rotated pages."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pymupdf as fitz


ROOT = Path(__file__).resolve().parents[1]


class RebuildPDFTest(unittest.TestCase):
    def run_script(self, script, *args):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), *map(str, args)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rotated_page_sizes_and_navigation_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source.pdf"
            output = base / "rebuilt.pdf"
            pages = base / "pages"
            with fitz.open() as document:
                for rotation in (0, 90, 180, 270):
                    page = document.new_page(width=80, height=120)
                    page.draw_rect(
                        fitz.Rect(5, 5, 30, 25), color=(1, 0, 0), fill=(1, 0, 0)
                    )
                    page.set_rotation(rotation)
                document.set_toc([[1, "First", 1], [1, "Rotated", 2]])
                document.set_metadata({"title": "Rotation sample", "author": "QA"})
                document.set_page_labels(
                    [{"startpage": 0, "prefix": "QA-", "style": "D", "firstpagenum": 1}]
                )
                document.save(source)

            self.run_script("extract_pages.py", source, pages, "--zoom", 1, "--workers", 1)
            self.run_script(
                "rebuild_pdf.py", pages, output, "--ref-pdf", source, "--target-width", 0
            )

            with fitz.open(source) as reference, fitz.open(output) as rebuilt:
                self.assertEqual(reference.page_count, rebuilt.page_count)
                for original, result in zip(reference, rebuilt):
                    with self.subTest(rotation=original.rotation):
                        self.assertEqual(result.rect.width, original.rect.width)
                        self.assertEqual(result.rect.height, original.rect.height)
                self.assertEqual(rebuilt.get_toc(), reference.get_toc())
                self.assertEqual(rebuilt.get_page_labels(), reference.get_page_labels())
                self.assertEqual(rebuilt.metadata["title"], reference.metadata["title"])
                self.assertEqual(rebuilt.metadata["author"], reference.metadata["author"])


if __name__ == "__main__":
    unittest.main()
