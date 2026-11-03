#!/usr/bin/env python3
"""Extract text per page, applying local OCR to scanned pages when needed."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def extract(source, output, mode="auto", language="spa+eng", max_pages=50):
    if mode not in ("auto", "force", "off") or language not in ("spa", "eng", "spa+eng"):
        raise ValueError("Modo o idioma OCR no válido.")
    output.mkdir(parents=True, exist_ok=True)
    texts, ocr_pages = [], []
    with tempfile.TemporaryDirectory(prefix="ocr-", dir=output.parent) as working:
        working = Path(working)
        is_pdf = source.suffix.lower() == ".pdf"
        if is_pdf:
            info = subprocess.check_output(["pdfinfo", str(source)], text=True, errors="replace")
            match = re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE)
            if not match:
                raise ValueError("No se pudo determinar el número de páginas.")
            pages = int(match[1])
        else:
            pages = 1
        if not 1 <= pages <= max_pages:
            raise ValueError(f"El OCR admite hasta {max_pages} páginas por trabajo. Divide el PDF.")
        for page in range(1, pages + 1):
            text = ""
            if is_pdf and mode != "force":
                text = subprocess.check_output(["pdftotext", "-f", str(page), "-l", str(page),
                                               "-enc", "UTF-8", "-layout", str(source), "-"], text=True, errors="replace")
            if mode != "off" and (not is_pdf or mode == "force" or len(text.strip()) < 25):
                if not shutil.which("tesseract"):
                    raise ValueError("Tesseract no está instalado.")
                image = working / "page.png"
                if is_pdf:
                    subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", "180",
                                    "-scale-to", "2400", "-singlefile", "-png", str(source), str(working / "page")], check=True)
                else:
                    subprocess.run(["ffmpeg", "-nostdin", "-y", "-protocol_whitelist", "file,pipe",
                                    "-format_whitelist", "image2,png_pipe,jpeg_pipe,webp_pipe", "-i", str(source),
                                    "-frames:v", "1", "-update", "1", str(image)], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                base = working / "recognized"
                subprocess.run(["tesseract", str(image), str(base), "-l", language, "--psm", "3", "txt"], check=True)
                text = base.with_suffix(".txt").read_text(encoding="utf-8", errors="replace")
                ocr_pages.append(page)
            elif not is_pdf:
                raise ValueError("Una imagen requiere OCR; selecciona Automático o Forzar OCR.")
            texts.append(text.strip())
        (output / "document.txt").write_text("\n\n".join(texts) + "\n", encoding="utf-8")
        (output / "ocr-info.json").write_text(json.dumps({"pages": pages, "ocr_pages": ocr_pages,
                                                         "language": language, "mode": mode}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("auto", "force", "off"), default="auto")
    parser.add_argument("--language", choices=("spa", "eng", "spa+eng"), default="spa+eng")
    args = parser.parse_args()
    extract(args.input, args.output, args.mode, args.language)


if __name__ == "__main__":
    main()
