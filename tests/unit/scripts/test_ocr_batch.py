"""Unit tests for scripts/ocr_batch.py (spec P9E-3) — engines are faked."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.ingestion.source import checksum_file

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ocr_batch.py"
_spec = importlib.util.spec_from_file_location("ocr_batch_under_test", _SCRIPT)
assert _spec is not None and _spec.loader is not None
ocr_batch = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ocr_batch
_spec.loader.exec_module(ocr_batch)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "ocr"
SCANNED_PDF = Path(__file__).resolve().parents[2] / "fixtures" / "parsing" / "pdf" / "scanned.pdf"

PDF_BYTES = b"%PDF-1.4 minimal test payload"


class FakeEngine:
    def __init__(self, *, explode_for: str | None = None) -> None:
        self.name = "paddleocr_vl_1_6"
        self.device = "cuda:0"
        self.version = "9.9.9-fake"
        self.explode_for = explode_for
        self.calls: list[str] = []

    def pages(self, pdf_path: Path) -> list:
        if self.explode_for is not None and self.explode_for in pdf_path.name:
            raise RuntimeError("simulated engine crash")
        self.calls.append(pdf_path.name)
        return [
            ocr_batch.PageResult(0, f"# Page {pdf_path.stem}\n\ntext zero", ocr_used=True),
            ocr_batch.PageResult(1, "| a | b |", ocr_used=True, warnings=["empty page text"]),
        ]


def make_args(tmp_path: Path, argv_extra: list[str]) -> SimpleNamespace:
    argv = [
        "--input-dir",
        str(tmp_path / "in"),
        "--output-dir",
        str(tmp_path / "out"),
        "--engine",
        "paddleocr_vl_1_6",
        "--device",
        "cuda:0",
        *argv_extra,
    ]
    return ocr_batch.parse_args(argv)


def seed_input(input_dir: Path, names: tuple[str, ...] = ("a.pdf", "b.pdf")) -> list[Path]:
    input_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names:
        path = input_dir / name
        path.write_bytes(PDF_BYTES + name.encode())
        paths.append(path)
    return paths


def test_parse_args_defaults_and_validation(tmp_path: Path) -> None:
    seed_input(tmp_path / "in")
    args = make_args(tmp_path, [])
    assert args.recursive is False
    assert args.force is False
    assert args.dry_run is False
    assert args.device == "cuda:0"
    with pytest.raises(SystemExit):
        ocr_batch.parse_args(
            ["--input-dir", str(tmp_path / "missing"), "--output-dir", "o", "--engine", "surya_2"]
        )
    with pytest.raises(SystemExit):
        make_args(tmp_path, ["--engine", "not_an_engine"])


def test_discovery_sorted_and_mirrored_naming(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    seed_input(input_dir, ("z.pdf", "a.pdf"))
    nested = input_dir / "sub" / "dir"
    nested.mkdir(parents=True)
    (nested / "deep.pdf").write_bytes(PDF_BYTES)

    assert ocr_batch.discover_pdfs(input_dir, recursive=False) == [
        input_dir / "a.pdf",
        input_dir / "z.pdf",
    ]
    deep_first = ocr_batch.discover_pdfs(input_dir, recursive=True)
    assert deep_first == sorted(deep_first) and len(deep_first) == 3

    md, sidecar = ocr_batch.output_paths_for(nested / "deep.pdf", input_dir, tmp_path / "out")
    assert md == (tmp_path / "out" / "sub" / "dir" / "deep.md").resolve()
    assert sidecar.name == "deep.ocr.json"


def test_committed_fixture_matches_sidecar_schema() -> None:
    sidecar = ocr_batch.OcrSidecar.model_validate(
        json.loads((FIXTURES / "scanned_sample.ocr.json").read_text(encoding="utf-8"))
    )
    assert sidecar.engine == "paddleocr_vl_1_6"
    assert sidecar.dry_run is False
    assert sidecar.source_checksum == checksum_file(SCANNED_PDF)
    markdown = (FIXTURES / "scanned_sample.md").read_text(encoding="utf-8")
    assert markdown.startswith("# ") and "|" in markdown
    with pytest.raises(ValueError):
        ocr_batch.OcrSidecar.model_validate(
            {
                **json.loads((FIXTURES / "scanned_sample.ocr.json").read_text(encoding="utf-8")),
                "unknown_field": 1,
            }
        )


def test_end_to_end_processed_and_idempotent_rerun(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    (pdf,) = seed_input(input_dir, ("only.pdf",))
    args = make_args(tmp_path, [])

    outcome = ocr_batch.process_pdf(pdf, args, FakeEngine())
    assert outcome.status == "processed"
    md_path, sidecar_path = ocr_batch.output_paths_for(pdf, input_dir, tmp_path / "out")
    markdown = md_path.read_text(encoding="utf-8")
    assert markdown.startswith("# Page only") and "| a | b |" in markdown
    sidecar = ocr_batch.read_sidecar(sidecar_path)
    assert sidecar is not None
    assert sidecar.pages_processed == 2 and sidecar.ocr_used is True
    assert sidecar.warnings == ["page 1: empty page text"]
    assert sidecar.source_checksum == checksum_file(pdf)

    engine = FakeEngine()
    rerun = ocr_batch.process_pdf(pdf, args, engine)
    assert rerun.status == "skipped" and "identical input checksum" in rerun.detail
    assert engine.calls == []

    forced = ocr_batch.process_pdf(pdf, make_args(tmp_path, ["--force"]), FakeEngine())
    assert forced.status == "processed"


def test_skip_rules_checksum_dry_run_and_missing_markdown(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    (pdf,) = seed_input(input_dir, ("doc.pdf",))
    _, sidecar_path = ocr_batch.output_paths_for(pdf, input_dir, tmp_path / "out")
    checksum = checksum_file(pdf)

    def write_sidecar(**overrides: object) -> None:
        payload = {
            "source_file": "doc.pdf",
            "source_checksum": checksum,
            "engine": "paddleocr_vl_1_6",
            "device": "cuda:0",
            "ocr_used": True,
            "pages_processed": 2,
            "finished_at": "2026-08-25T00:00:00+00:00",
        }
        payload.update(overrides)
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(json.dumps(payload), encoding="utf-8")

    assert ocr_batch.should_skip(pdf, sidecar_path, checksum, force=False)[0] is False

    write_sidecar()
    assert ocr_batch.should_skip(pdf, sidecar_path, checksum, force=False)[0] is False

    md_path = sidecar_path.with_name("doc.md")
    md_path.write_text("# doc\n", encoding="utf-8")
    assert ocr_batch.should_skip(pdf, sidecar_path, checksum, force=False)[0] is True

    other = pdf.with_name("other.pdf")
    other.write_bytes(b"%PDF different")
    assert (
        "changed"
        in ocr_batch.should_skip(other, sidecar_path, checksum_file(other), force=False)[1]
    )

    write_sidecar(dry_run=True)
    assert "planned" in ocr_batch.should_skip(pdf, sidecar_path, checksum, force=False)[1]


def test_per_file_failure_isolation(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    good, bad = seed_input(input_dir, ("good.pdf", "bad.pdf"))
    args = make_args(tmp_path, [])
    engine = FakeEngine(explode_for="bad")

    outcomes = ocr_batch.run_batch(args, engine)
    statuses = {o.pdf.name: o.status for o in outcomes}
    assert statuses == {"good.pdf": "processed", "bad.pdf": "failed"}
    assert "RuntimeError" in next(o.detail for o in outcomes if o.pdf == bad)
    assert (tmp_path / "out" / "good.md").is_file()
    assert len(engine.calls) == 1


def test_dry_run_plans_without_engine_or_markdown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    input_dir = tmp_path / "in"
    seed_input(input_dir, ("x.pdf", "y.pdf"))
    argv = [
        "--input-dir",
        str(input_dir),
        "--output-dir",
        str(tmp_path / "out"),
        "--engine",
        "surya_2",
        "--dry-run",
    ]

    assert ocr_batch.main(argv) == 0
    capsys.readouterr()
    out = tmp_path / "out"
    assert not (out / "x.md").exists() and not (out / "y.md").exists()
    for stem in ("x", "y"):
        sidecar = ocr_batch.read_sidecar(out / f"{stem}.ocr.json")
        assert sidecar is not None
        assert sidecar.dry_run is True and sidecar.ocr_used is False
        assert sidecar.pages_processed == 0

    report = json.loads((out / "ocr_batch_report.json").read_text(encoding="utf-8"))
    assert report["counts"] == {"processed": 0, "skipped": 0, "planned": 2, "failed": 0}


def test_main_wiring_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    input_dir = tmp_path / "in"
    seed_input(input_dir, ("m.pdf",))

    dry_argv = [
        "--input-dir",
        str(input_dir),
        "--output-dir",
        str(tmp_path / "out"),
        "--engine",
        "surya_2",
        "--dry-run",
    ]
    assert ocr_batch.main(dry_argv) == 0
    assert "report:" in capsys.readouterr().out

    with pytest.raises(SystemExit) as exc_info:
        ocr_batch.main(
            [
                "--input-dir",
                str(tmp_path / "nope"),
                "--output-dir",
                str(tmp_path / "out"),
                "--engine",
                "surya_2",
            ]
        )
    assert exc_info.value.code == 2


def test_dry_run_never_overwrites_real_sidecar(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    (pdf,) = seed_input(input_dir, ("real.pdf",))
    args_real = make_args(tmp_path, [])
    engine = FakeEngine()
    # First do a real run
    out_real = ocr_batch.process_pdf(pdf, args_real, engine)
    assert out_real.status == "processed"
    _, sidecar_path = ocr_batch.output_paths_for(pdf, input_dir, tmp_path / "out")
    initial_content = sidecar_path.read_text(encoding="utf-8")
    assert '"dry_run": false' in initial_content

    # Now run dry-run with force
    args_dry = make_args(tmp_path, ["--dry-run", "--force"])
    out_dry = ocr_batch.process_pdf(pdf, args_dry, engine)
    assert out_dry.status == "planned"
    after_content = sidecar_path.read_text(encoding="utf-8")
    assert after_content == initial_content
    assert '"dry_run": false' in after_content
