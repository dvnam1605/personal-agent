"""Unit tests for scripts/ingestion/ocr_vl — the GPU-free parts of the fine-tuned VL OCR path."""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path

import pytest
from PIL import Image

from scripts.ingestion.ocr_vl import checkpoint, layout, markdown, pipeline, recognizer
from scripts.ingestion.ocr_vl.layout import Block
from scripts.ingestion.ocr_vl.table_split import split_tables


def _box(label: str, x1: float, y1: float, x2: float, y2: float, order: int | None = None) -> dict:
    return {"label": label, "score": 0.9, "coordinate": [x1, y1, x2, y2], "order": order}


def test_layout_orders_blocks_and_skips_inline_formulas() -> None:
    payload = {
        "res": {
            "boxes": [
                _box("text", 0, 100, 500, 130, order=2),
                _box("doc_title", 0, 0, 500, 40, order=1),
                _box("inline_formula", 10, 105, 40, 125, order=3),
            ]
        }
    }
    blocks = layout.blocks_from_layout(payload)
    assert [b.label for b in blocks] == ["doc_title", "text"]
    assert blocks[0].task == "ocr" and blocks[0].score == 0.9


def test_layout_drops_wrapping_text_and_merges_formula_lines() -> None:
    wrapper = Block("text", [0, 0, 400, 60])
    lines = [Block("text", [0, 0, 400, 28]), Block("text", [0, 30, 400, 58])]
    assert layout.drop_wrapping_text([wrapper, *lines]) == lines

    first = Block("display_formula", [100, 0, 300, 20])
    second = Block("display_formula", [102, 22, 320, 42])
    merged = layout.merge_formula_lines([first, second])
    assert len(merged) == 1 and merged[0].box == [100, 0, 320, 42]
    far = Block("display_formula", [100, 200, 300, 220])
    assert len(layout.merge_formula_lines([Block("display_formula", [100, 0, 300, 20]), far])) == 2


def test_layout_trims_table_that_runs_into_caption_line() -> None:
    table = Block("table", [0, 0, 500, 400])
    caption = Block("text", [0, 390, 500, 420])
    layout.trim_tables([table, caption])
    assert table.box[3] == 388


def test_otsl_to_html_spans_padding_and_cell_latex() -> None:
    otsl = "<fcel>A<lcel><fcel>B<nl><fcel>x \\leq 2<ecel><ucel><nl><fcel>short<nl>"
    html = markdown.otsl_to_html(otsl)
    assert html.startswith('<table><tr><td colspan="2">A</td><td rowspan="3">B</td></tr>')
    assert "<tr><td>x ≤ 2</td><td></td></tr>" in html
    assert html.endswith("<tr><td>short</td><td></td></tr></table>")
    assert markdown.cell_text("  $5$ \\%  ") == "5 %"


def test_fix_tex_repairs_dots_and_open_braces() -> None:
    assert markdown.fix_tex("a \\cdot \\cdot b") == "a \\cdots b"
    assert markdown.fix_tex("\\frac{a}{b") == "\\frac{a}{b}"


def test_block_markdown_per_label() -> None:
    assert markdown.block_markdown(
        Block("doc_title", [0, 0, 1, 1], text="QUYẾT ĐỊNH\nVề việc")
    ) == ("# QUYẾT ĐỊNH<br>Về việc")
    assert markdown.block_markdown(Block("paragraph_title", [0, 0, 1, 1], text="1. Mục")) == (
        "## 1. Mục"
    )
    assert markdown.block_markdown(Block("figure_title", [0, 0, 1, 1], text="Hình 1")) == "*Hình 1*"
    assert markdown.block_markdown(Block("display_formula", [0, 0, 1, 1], text="\\[x^2\\]")) == (
        "$$\nx^2\n$$"
    )
    assert markdown.block_markdown(Block("text", [0, 0, 1, 1], text="\\frac{1}{2}")).startswith(
        "$$"
    )
    assert markdown.block_markdown(Block("table", [0, 0, 1, 1], text="<fcel>1<nl>")) == (
        "<table><tr><td>1</td></tr></table>"
    )
    assert markdown.block_markdown(Block("image", [0, 0, 1, 1], text="")) == ""
    assert markdown.block_markdown(Block("text", [0, 0, 1, 1], text="   ")) == ""


def test_page_markdown_groups_side_by_side_headers_and_drops_margins() -> None:
    blocks = [
        Block("header", [0, 0, 1000, 20], text="Trang đầu"),
        Block("text", [0, 40, 400, 80], text="BỘ TÀI CHÍNH"),
        Block("text", [600, 40, 1000, 80], text="CỘNG HÒA XÃ HỘI"),
        Block("doc_title", [0, 200, 1000, 260], text="QUYẾT ĐỊNH"),
        Block("text", [0, 300, 1000, 900], text="Nội dung dài."),
        Block("number", [480, 950, 520, 980], text="1"),
    ]
    page = markdown.page_markdown(blocks)
    assert page.startswith("<table><tr><td>BỘ TÀI CHÍNH</td><td>CỘNG HÒA XÃ HỘI</td></tr></table>")
    assert "# QUYẾT ĐỊNH" in page and "Nội dung dài." in page
    assert "Trang đầu" not in page and page.rstrip().endswith("Nội dung dài.")
    assert "Trang đầu" in markdown.page_markdown(blocks, keep_margins=True)
    assert markdown.page_markdown([Block("text", [0, 0, 1, 1])]) == ""


def test_join_page_tables_repeated_header_data_row_and_unrelated_table() -> None:
    header = "<tr><td>STT</td><td>Tên</td></tr>"
    first = f"Mở đầu\n\n<table>{header}<tr><td>1</td><td>A</td></tr></table>"
    repeated = f"<table>{header}<tr><td>2</td><td>B</td></tr></table>\n\nTiếp"
    joined = markdown.join_page_tables([first, repeated])
    assert joined[0].endswith(
        f"<table>{header}<tr><td>1</td><td>A</td></tr><tr><td>2</td><td>B</td></tr></table>"
    )
    assert joined[1] == "Tiếp"

    continued = "<table><tr><td>3</td><td>C</td></tr></table>"
    assert "<td>3</td>" in markdown.join_page_tables([first, continued])[0]

    other = "<table><tr><td>Mục</td><td>Ghi chú</td></tr></table>"
    assert markdown.join_page_tables([first, other]) == [first, other]


def test_split_tables_turns_heading_rows_into_headings() -> None:
    rows = [
        ("Khoản mục", "Số cuối kỳ"),
        ("Tiền mặt", "1.000"),
        ("3c. Tài sản tài chính như sau:", ""),
        ("Cổ phiếu", "2.000"),
        ("Trái phiếu", "3.000"),
    ]
    table = "<table>" + "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in rows) + "</table>"
    pieces = split_tables(table).split("\n\n")
    assert pieces[1] == "## 3c. Tài sản tài chính như sau:"
    assert pieces[0].startswith("<table>") and "Tiền mặt" in pieces[0]
    assert pieces[2].startswith("<table>") and "Trái phiếu" in pieces[2]
    assert (
        split_tables("<table><tr><td>1</td></tr></table>") == "<table><tr><td>1</td></tr></table>"
    )


def test_plan_batches_puts_large_text_with_tables() -> None:
    items = [(100, "ocr"), (900, "table"), (50, "ocr"), (700, "ocr"), (800, "chart")]
    plan = recognizer.plan_batches(items)
    assert plan[0] == [1, 4, 3, 0, 2]
    many = recognizer.plan_batches([(i, "ocr") for i in range(10)])
    assert [len(batch) for batch in many] == [8, 2] and many[0][0] == 9
    assert recognizer.plan_batches([]) == []


def test_recognizer_read_assigns_text_in_input_order(monkeypatch: pytest.MonkeyPatch) -> None:
    vl = recognizer.VlRecognizer(Path("unused"), "cpu")
    monkeypatch.setattr(vl, "load", lambda: None)
    monkeypatch.setattr(
        vl, "_run_batch", lambda items: [f"{task}:{img.width}" for img, task in items]
    )
    crops = [(Image.new("RGB", (10, 5)), "ocr"), (Image.new("RGB", (40, 40)), "table")]
    assert vl.read(crops) == ["ocr:10", "table:40"]
    assert vl.read([]) == []


def _write_checkpoint(folder: Path, weights: bytes = b"weights") -> Path:
    folder.mkdir(parents=True)
    (folder / "config.json").write_text("{}", encoding="utf-8")
    (folder / "model.safetensors").write_bytes(weights)
    return folder


def test_checkpoint_validation_name_and_hash(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a directory"):
        checkpoint.validate_checkpoint(tmp_path / "missing")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="config.json"):
        checkpoint.validate_checkpoint(tmp_path / "empty")
    (tmp_path / "empty" / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="safetensors"):
        checkpoint.validate_checkpoint(tmp_path / "empty")

    ckpt = _write_checkpoint(tmp_path / "round11_out" / "ckpt")
    other = _write_checkpoint(tmp_path / "ckpt_round12", b"other weights")
    assert checkpoint.default_model_name(ckpt) == "round11_out"
    assert checkpoint.default_model_name(other) == "ckpt_round12"
    identity = checkpoint.describe_checkpoint(ckpt)
    assert identity.short() == f"round11_out sha256:{identity.sha256[:12]}"
    assert checkpoint.describe_checkpoint(ckpt, "round11").name == "round11"
    assert checkpoint.checkpoint_digest(other) != identity.sha256


def test_crop_blocks_upscales_small_text_and_skips_images() -> None:
    page = Image.new("RGB", (1000, 800), "white")
    text = Block("text", [100, 100, 500, 120])
    table = Block("table", [100, 200, 700, 500])
    image = Block("image", [100, 600, 300, 700])
    sliver = Block("text", [1002, 0, 1010, 10])
    crops = pipeline.crop_blocks(page, [text, table, image, sliver])
    assert [b for b, _ in crops] == [text, table]
    assert crops[0][1].height == 48
    assert crops[1][1].size == (1216, 616)
    assert image.text == "" and sliver.text == ""

    hi_page = Image.new("RGB", (1500, 1200), "white")
    hi_crops = pipeline.crop_blocks(page, [Block("text", [100, 100, 500, 200])], hi_page)
    assert hi_crops[0][1].size == (612, 162)


class _FakePaddle:
    def __init__(self, *, angle: int = 0, fail: bool = False) -> None:
        self.angle = angle
        self.fail = fail

    def page_orientation(self, png: Path) -> tuple[int, float]:
        return self.angle, 0.9

    def line_votes(self, image: Image.Image) -> tuple[int, int]:
        return 0, 5

    def layout(self, png: Path) -> list[Block]:
        if self.fail:
            raise RuntimeError("layout crashed")
        return [Block("doc_title", [10, 10, 190, 40]), Block("text", [10, 60, 190, 90])]


class _FakeRecognizer:
    def read(self, items: list[tuple[Image.Image, str]]) -> list[str]:
        return [f"read {task}" for _, task in items]

    def release_cache(self) -> None:
        return None


def _vl_pipeline(monkeypatch: pytest.MonkeyPatch, paddle: _FakePaddle) -> pipeline.VlPipeline:
    monkeypatch.setenv("DISABLE_MODEL_SOURCE_CHECK", "True")
    monkeypatch.setenv("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    vl = pipeline.VlPipeline(Path("unused"), "cpu")
    monkeypatch.setattr(vl, "paddle", paddle)
    monkeypatch.setattr(vl, "recognizer", _FakeRecognizer())
    return vl


def test_prepare_turns_page_and_reports_layout_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    png = tmp_path / "page.png"
    Image.new("RGB", (200, 100), "white").save(png)
    vl = _vl_pipeline(monkeypatch, _FakePaddle(angle=90))
    prepared = vl._prepare(pipeline.RenderedPage(0, png))
    with Image.open(png) as turned:
        assert turned.size == (100, 200)
    assert [b.label for b, _ in prepared.crops] == ["doc_title", "text"]
    assert prepared.warnings == []

    broken = _vl_pipeline(monkeypatch, _FakePaddle(fail=True))
    failed = broken._prepare(pipeline.RenderedPage(1, png))
    assert failed.crops == [] and failed.warnings == ["layout failed: RuntimeError: layout crashed"]
    vl.close()
    broken.close()


def test_read_all_writes_markdown_and_flags_empty_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    vl = _vl_pipeline(monkeypatch, _FakePaddle())
    page = pipeline.PreparedPage(0, blocks=[Block("doc_title", [0, 0, 100, 30])])
    page.crops = [(page.blocks[0], Image.new("RGB", (100, 30)))]
    empty = pipeline.PreparedPage(1, warnings=["layout failed: boom"])
    futures: list[Future[pipeline.PreparedPage]] = []
    for prepared in (page, empty):
        future: Future[pipeline.PreparedPage] = Future()
        future.set_result(prepared)
        futures.append(future)
    pages = vl._read_all(futures)
    assert [p.markdown for p in pages] == ["# read ocr", ""]
    assert pages[1].warnings == ["layout failed: boom", "empty page text"]
    vl.close()
