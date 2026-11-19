"""Offline OCR batch script: folder of scanned PDFs -> folder of Markdown (spec P9E).

Runs OUTSIDE the assistant runtime on a strong-GPU machine, one-shot, never in
the request path. Output feeds ingestion through
``source_type="preparsed_markdown"``; the sidecar metadata (SHA-256 PDF checksum,
OCR engine and version) is loaded alongside the Markdown and incorporated into the
orchestrator's fingerprint inputs, ensuring provenance integrity and proper cache invalidation.

Engines:

- ``paddleocr_vl_1_6``: the fine-tuned PaddleOCR-VL 1.6 checkpoint given by ``--model-path``
  (a local transformers folder, e.g. round 11), run through the scan pipeline in ``ocr_vl/``.
  ``engine_version`` names the checkpoint and its weight hash, so a new checkpoint changes the
  ingestion fingerprint and re-OCRs files on the next batch.
- ``paddle_ppstructurev3``: stock PP-StructureV3 from ``paddleocr``.
- ``surya_2``: Surya OCR.

Dependency isolation (P9E-2, option a): paddle/torch/surya live in optional groups
(``pip install -e ".[ocr-paddle]"`` / ``".[ocr-surya]"``) installed only on the
external machine; imports happen lazily inside engine constructors so CI and
the runtime suite never touch them.

Usage::

    python scripts/ingestion/ocr_batch.py \
        --input-dir ./scanned_pdfs --output-dir ./parsed_md \
        --engine paddleocr_vl_1_6 --model-path /path/to/round11/ckpt --model-name round11 \
        --device cuda:0 [--vram-gb 5.5] [--recursive] [--force]

Exit codes: 0 = every file processed or skipped, 1 = at least one failure
(the batch itself always runs to completion), 2 = usage/configuration error.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

from app.services.ingestion.source import checksum_file  # noqa: E402
from scripts.ingestion.ocr_vl import (  # noqa: E402
    PIPELINE_VERSION,
    VlPipeline,
    describe_checkpoint,
    validate_checkpoint,
)

SIDECAR_SUFFIX = ".ocr.json"
SIDECAR_SCHEMA_VERSION = 1
VL_ENGINE = "paddleocr_vl_1_6"
ENGINES = (VL_ENGINE, "paddle_ppstructurev3", "surya_2")
ENGINE_DEPENDENCIES = {
    VL_ENGINE: ("paddleocr", "torch", "transformers", "einops"),
    "paddle_ppstructurev3": ("paddleocr",),
    "surya_2": ("surya-ocr",),
}
MODEL_PATH_ENV = "OCR_VL_MODEL_PATH"
DEFAULT_VRAM_GB = 5.5


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
    """Lazy engine wrapper.

    :meth:`load` checks the checkpoint and the installed dependencies and fixes ``version``
    (what the sidecar records); models load on the first :meth:`pages` call and stay loaded
    for the whole batch.
    """

    def __init__(
        self,
        name: str,
        device: str,
        *,
        model_path: Path | None = None,
        model_name: str | None = None,
        vram_gb: float | None = None,
    ) -> None:
        self.name = name
        self.device = device
        self.model_path = model_path
        self.model_name = model_name
        self.vram_gb = vram_gb
        self.version: str | None = None
        self._pipeline: VlPipeline | None = None

    def load(self) -> None:
        if self.name != VL_ENGINE:
            dependency = ENGINE_DEPENDENCIES[self.name][0]
            self.version = _dependency_versions(ENGINE_DEPENDENCIES[self.name])[dependency]
            return
        model_path = self._require_model_path()
        try:
            validate_checkpoint(model_path)
        except ValueError as error:
            raise RuntimeError(str(error)) from None
        versions = _dependency_versions(ENGINE_DEPENDENCIES[VL_ENGINE])
        identity = describe_checkpoint(model_path, self.model_name)
        self.version = (
            f"{identity.short()}; paddleocr {versions['paddleocr']}; pipeline {PIPELINE_VERSION}"
        )

    def pages(self, pdf_path: Path) -> list[PageResult]:
        if self.name == VL_ENGINE:
            return self._vl_pages(pdf_path)
        if self.name == "surya_2":
            return _surya_pages(pdf_path, self.device)
        return _ppstructure_pages(pdf_path, self.device)

    def close(self) -> None:
        if self._pipeline is not None:
            self._pipeline.close()
            self._pipeline = None

    def _require_model_path(self) -> Path:
        if self.model_path is None:
            msg = f"engine '{VL_ENGINE}' needs --model-path (or ${MODEL_PATH_ENV})"
            raise RuntimeError(msg)
        return self.model_path

    def _vl_pages(self, pdf_path: Path) -> list[PageResult]:
        if self._pipeline is None:
            self._pipeline = VlPipeline(self._require_model_path(), self.device, self.vram_gb)
        return [
            PageResult(page.index, page.markdown, ocr_used=True, warnings=page.warnings)
            for page in self._pipeline.run(pdf_path)
        ]


def _dependency_versions(distributions: tuple[str, ...]) -> dict[str, str]:
    versions: dict[str, str] = {}
    missing: list[str] = []
    for distribution in distributions:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            missing.append(distribution)
    if missing:
        msg = (
            f"engine dependencies not installed: {', '.join(missing)}; use [ocr-paddle]/[ocr-surya]"
        )
        raise RuntimeError(msg)
    return versions


def _ppstructure_pages(pdf_path: Path, device: str) -> list[PageResult]:
    from paddleocr import PPStructureV3

    pipeline: Any = PPStructureV3(device="gpu:0" if device.startswith("cuda") else "cpu")
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
    parser.add_argument(
        "--model-path",
        type=Path,
        default=os.environ.get(MODEL_PATH_ENV) or None,
        help=f"{VL_ENGINE}: local fine-tuned checkpoint folder (default: ${MODEL_PATH_ENV})",
    )
    parser.add_argument(
        "--model-name",
        default=None,
        help=f"{VL_ENGINE}: name recorded in the sidecar (default: checkpoint folder name)",
    )
    parser.add_argument(
        "--vram-gb",
        type=float,
        default=DEFAULT_VRAM_GB,
        help=f"{VL_ENGINE}: cap on GPU memory for this process (default: {DEFAULT_VRAM_GB})",
    )
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
    if args.engine == VL_ENGINE and not args.dry_run and args.model_path is None:
        parser.error(f"--engine {VL_ENGINE} needs --model-path (or ${MODEL_PATH_ENV})")
    if args.model_path is not None:
        args.model_path = Path(args.model_path)
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


def md_path_for_sidecar(sidecar_path: Path) -> Path:
    """Robustly derive markdown path from sidecar path."""
    if sidecar_path.name.endswith(SIDECAR_SUFFIX):
        stem = sidecar_path.name[: -len(SIDECAR_SUFFIX)]
    else:
        stem = sidecar_path.stem
    return sidecar_path.with_name(f"{stem}.md")


def read_sidecar(path: Path) -> OcrSidecar | None:
    try:
        return OcrSidecar.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def should_skip(
    pdf: Path,
    sidecar_path: Path,
    checksum: str,
    *,
    force: bool,
    engine_name: str | None = None,
    engine_version: str | None = None,
) -> tuple[bool, str]:
    """Idempotent re-run rule: matching checksum, same engine/model, real (non-dry-run) output.

    The engine check applies only when the current engine's version is known (not in dry-run).
    """
    if force:
        return False, "forced re-run"
    sidecar = read_sidecar(sidecar_path)
    if sidecar is None:
        return False, "no readable sidecar"
    if sidecar.dry_run:
        return False, "only a planned (dry-run) sidecar exists"
    if sidecar.source_checksum != checksum:
        return False, "input changed since last run"
    if engine_version is not None and (
        sidecar.engine != engine_name or sidecar.engine_version != engine_version
    ):
        return False, "OCR engine or model changed since last run"
    if not md_path_for_sidecar(sidecar_path).is_file():
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

    skip, reason = should_skip(
        pdf,
        sidecar_path,
        checksum,
        force=args.force,
        engine_name=engine.name,
        engine_version=engine.version,
    )
    if skip:
        return FileOutcome(pdf, "skipped", reason)

    existing_sidecar = read_sidecar(sidecar_path)
    if args.dry_run and existing_sidecar is not None and not existing_sidecar.dry_run:
        # Never overwrite an existing real sidecar during dry run
        return FileOutcome(
            pdf, "planned", "dry-run: planned rerun (existing real sidecar preserved)"
        )

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


def write_report(
    outcomes: list[FileOutcome], output_dir: Path, input_dir: Path | None = None
) -> Path:
    def _rel_pdf(p: Path) -> str:
        if input_dir is not None:
            try:
                return p.relative_to(input_dir).as_posix()
            except ValueError:
                pass
        return p.name

    report = {
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "counts": {
            key: sum(1 for o in outcomes if o.status == key)
            for key in ("processed", "skipped", "planned", "failed")
        },
        "files": [
            {"pdf": _rel_pdf(o.pdf), "status": o.status, "detail": o.detail} for o in outcomes
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "ocr_batch_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    engine = OcrEngine(
        args.engine,
        args.device,
        model_path=args.model_path,
        model_name=args.model_name,
        vram_gb=args.vram_gb,
    )
    if not args.dry_run:
        try:
            engine.load()
        except RuntimeError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
    try:
        outcomes = run_batch(args, engine)
    finally:
        engine.close()
    if outcomes:
        report_path = write_report(outcomes, args.output_dir, input_dir=args.input_dir)
        print(f"report: {report_path}")
    failures = sum(1 for o in outcomes if o.status == "failed")
    print(f"done: {len(outcomes)} file(s), {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
