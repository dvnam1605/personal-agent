"""Offline OCR batch script: folder of scanned PDFs -> folder of Markdown (spec P9E).

Runs OUTSIDE the assistant runtime on a strong-GPU machine, one-shot, never in
the request path. Output feeds ingestion through
``source_type="preparsed_markdown"``; the sidecar ``source_checksum`` (SHA-256
of the original PDF) is what the orchestrator fingerprints.

Dependency isolation (P9E-2, option a): paddle/surya live in optional groups
(``pip install -e ".[ocr-paddle]"`` / ``".[ocr-surya]"``) installed only on the
external machine; imports happen lazily inside engine constructors so CI and
the runtime suite never touch them. Engine glue is smoke-validated on the GPU
machine per spec P9E-3 — this environment verifies everything around it.

Usage::

    python scripts/ocr_batch.py \
        --input-dir ./scanned_pdfs --output-dir ./parsed_md \
        --engine paddleocr_vl_1_6 --device cuda:0 [--recursive] [--force]

Exit codes: 0 = every file processed or skipped, 1 = at least one failure
(the batch itself always runs to completion), 2 = usage/configuration error.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

from app.services.ingestion.source import checksum_file  # noqa: E402

SIDECAR_SUFFIX = ".ocr.json"
SIDECAR_SCHEMA_VERSION = 1
ENGINES = ("paddleocr_vl_1_6", "paddle_ppstructurev3", "surya_2")


class OcrSidecar(BaseModel):
    """Per-output metadata contract consumed alongside the Markdown file."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SIDECAR_SCHEMA_VERSION
    source_file: str
    source_checksum: str = Field(min_length=64, max_length=64)
    engine: str
    engine_version: str | None = None
    device: str
    ocr_used: bool
    pages_processed: int = 0
    warnings: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    dry_run: bool = False
    finished_at: str


class PageResult:
    """One processed page: markdown text plus provenance flags."""

    __slots__ = ("index", "markdown", "ocr_used", "warnings")

    def __init__(
        self,
        index: int,
        markdown: str,
        *,
        ocr_used: bool,
        warnings: list[str] | None = None,
    ) -> None:
        self.index = index
        self.markdown = markdown
        self.ocr_used = ocr_used
        self.warnings = warnings or []


class OcrEngine:
    """Lazy engine wrapper; heavy imports happen in :meth:`load`."""

    def __init__(self, name: str, device: str) -> None:
        self.name = name
        self.device = device
        self.version: str | None = None

    def load(self) -> None:
        dist = {
            "paddleocr_vl_1_6": "paddleocr",
            "paddle_ppstructurev3": "paddleocr",
            "surya_2": "surya-ocr",
        }[self.name]
        try:
            self.version = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            msg = f"engine dependency '{dist}' not installed; use [ocr-paddle]/[ocr-surya]"
            raise RuntimeError(msg) from None

    def pages(self, pdf_path: Path) -> list[PageResult]:
        if self.name == "surya_2":
            return _surya_pages(pdf_path, self.device)
        return _paddle_pages(pdf_path, self.name, self.device)


def _paddle_pages(pdf_path: Path, engine_name: str, device: str) -> list[PageResult]:
    from paddleocr import PaddleOCRVL, PPStructureV3

    pipeline_kwargs = {"device": "gpu:0" if device.startswith("cuda") else "cpu"}
    pipeline = (
        PPStructureV3(**pipeline_kwargs)
        if engine_name == "paddle_ppstructurev3"
        else PaddleOCRVL(**pipeline_kwargs)
    )
    results: list[PageResult] = []
    for index, res in enumerate(pipeline.predict(str(pdf_path), use_textline_orientation=True)):
        markdown = getattr(res, "markdown", None)
        text = ""
        if isinstance(markdown, dict):
            inner = markdown.get("markdown") or markdown.get("text")
            if isinstance(inner, dict):
                text = str(inner.get("text") or "")
            else:
                text = str(inner or "")
        elif markdown is not None:
            text = str(markdown)
        warnings = ["empty page text"] if not text.strip() else []
        results.append(PageResult(index, text, ocr_used=True, warnings=warnings))
    return results


def _surya_pages(pdf_path: Path, device: str) -> list[PageResult]:
    from surya.detection import DetectionPredictor
    from surya.input.processing import get_page_images, open_pdf
    from surya.recognition import RecognitionPredictor

    det = DetectionPredictor()
    rec = RecognitionPredictor()
    doc = open_pdf(str(pdf_path))
    results: list[PageResult] = []
    try:
        for index in range(len(doc)):
            image = get_page_images(doc, indices=[index], render_device=device)[0]
            prediction = rec([image], [["en"]], det, return_words=False)
            text = "\n\n".join(line.text for line in prediction[0].text_lines)
            warnings = ["empty page text"] if not text.strip() else []
            results.append(PageResult(index, text, ocr_used=True, warnings=warnings))
    finally:
        doc.close()
    return results


@dataclass
class FileOutcome:
    pdf: Path
    status: str  # "processed" | "skipped" | "planned" | "failed"
    detail: str = ""
    sidecar: OcrSidecar | None = None
    warnings: list[str] = field(default_factory=list)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline OCR batch: scanned PDFs folder -> Markdown folder (spec P9E).",
    )
    parser.add_argument("--input-dir", required=True, type=Path, help="folder of scanned *.pdf")
    parser.add_argument("--output-dir", required=True, type=Path, help="mirrored Markdown tree")
    parser.add_argument("--engine", required=True, choices=ENGINES)
    parser.add_argument("--device", default="cuda:0", help="cuda:0 | cpu (default: cuda:0)")
    parser.add_argument("--recursive", action="store_true", help="walk input-dir recursively")
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-OCR even when a matching idempotent sidecar exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="plan without OCR: walk inputs and write planned sidecars only",
    )
    args = parser.parse_args(argv)
    if not args.input_dir.is_dir():
        parser.error(f"--input-dir is not a directory: {args.input_dir}")
    return args


def discover_pdfs(input_dir: Path, *, recursive: bool) -> list[Path]:
    pattern = "**/*.pdf" if recursive else "*.pdf"
    return sorted(input_dir.glob(pattern))


def output_paths_for(pdf: Path, input_dir: Path, output_dir: Path) -> tuple[Path, Path]:
    """Mirrored-tree naming: <stem>.md + <stem>.ocr.json under output-dir."""
    relative = pdf.relative_to(input_dir)
    md_path = (output_dir / relative.with_suffix(".md")).resolve()
    sidecar_path = md_path.parent / (md_path.stem + SIDECAR_SUFFIX)
    return md_path, sidecar_path


def read_sidecar(path: Path) -> OcrSidecar | None:
    try:
        return OcrSidecar.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def should_skip(pdf: Path, sidecar_path: Path, checksum: str, *, force: bool) -> tuple[bool, str]:
    """Idempotent re-run rule: matching checksum + real (non-dry-run) output."""
    if force:
        return False, "forced re-run"
    sidecar = read_sidecar(sidecar_path)
    if sidecar is None:
        return False, "no readable sidecar"
    if sidecar.dry_run:
        return False, "only a planned (dry-run) sidecar exists"
    if sidecar.source_checksum != checksum:
        return False, "input changed since last run"
    if not sidecar_path.with_name(sidecar_path.name[: -len(SIDECAR_SUFFIX)] + ".md").is_file():
        return False, "sidecar present but Markdown missing"
    return True, "already processed with identical input checksum"


def build_sidecar(
    *,
    pdf: Path,
    input_dir: Path,
    checksum: str,
    engine: OcrEngine,
    pages_processed: int,
    ocr_used: bool,
    warnings: list[str],
    duration_seconds: float,
    dry_run: bool,
) -> OcrSidecar:
    return OcrSidecar(
        source_file=pdf.relative_to(input_dir).as_posix(),
        source_checksum=checksum,
        engine=engine.name,
        engine_version=engine.version,
        device=engine.device,
        ocr_used=ocr_used,
        pages_processed=pages_processed,
        warnings=warnings,
        duration_seconds=duration_seconds,
        dry_run=dry_run,
        finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def process_pdf(pdf: Path, args: argparse.Namespace, engine: OcrEngine) -> FileOutcome:
    started = time.perf_counter()
    checksum = checksum_file(pdf)
    md_path, sidecar_path = output_paths_for(pdf, args.input_dir, args.output_dir)

    if not args.dry_run:
        skip, reason = should_skip(pdf, sidecar_path, checksum, force=args.force)
        if skip:
            return FileOutcome(pdf, "skipped", reason)

    md_path.parent.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    pages_processed = 0
    ocr_used = False

    if not args.dry_run:
        parts: list[str] = []
        for page in engine.pages(pdf):
            parts.append(page.markdown)
            warnings.extend(f"page {page.index}: {w}" for w in page.warnings)
            ocr_used = ocr_used or page.ocr_used
            pages_processed += 1
        md_path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")

    duration = round(time.perf_counter() - started, 3)
    sidecar = build_sidecar(
        pdf=pdf,
        input_dir=args.input_dir,
        checksum=checksum,
        engine=engine,
        pages_processed=pages_processed,
        ocr_used=ocr_used,
        warnings=warnings,
        duration_seconds=duration,
        dry_run=args.dry_run,
    )
    sidecar_path.write_text(sidecar.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return FileOutcome(pdf, "planned" if args.dry_run else "processed", sidecar=sidecar)


def run_batch(args: argparse.Namespace, engine: OcrEngine) -> list[FileOutcome]:
    outcomes: list[FileOutcome] = []
    for pdf in discover_pdfs(args.input_dir, recursive=args.recursive):
        try:
            outcome = process_pdf(pdf, args, engine)
        except Exception as error:  # noqa: BLE001 - one failed PDF must not abort the batch
            outcome = FileOutcome(pdf, "failed", f"{type(error).__name__}: {error}")
        outcomes.append(outcome)
        print(f"[{outcome.status:>9}] {pdf.relative_to(args.input_dir)} {outcome.detail}".rstrip())
    return outcomes


def write_report(outcomes: list[FileOutcome], output_dir: Path) -> Path:
    report = {
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "counts": {
            key: sum(1 for o in outcomes if o.status == key)
            for key in ("processed", "skipped", "planned", "failed")
        },
        "files": [{"pdf": o.pdf.name, "status": o.status, "detail": o.detail} for o in outcomes],
    }
    report_path = output_dir / "ocr_batch_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    engine = OcrEngine(args.engine, args.device)
    if not args.dry_run:
        try:
            engine.load()
        except RuntimeError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
    outcomes = run_batch(args, engine)
    if outcomes:
        report_path = write_report(outcomes, args.output_dir)
        print(f"report: {report_path}")
    failures = sum(1 for o in outcomes if o.status == "failed")
    print(f"done: {len(outcomes)} file(s), {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
