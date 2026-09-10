"""P9C chunking-engine unit tests (parent boundaries, IDs, ladder, tables)."""

from __future__ import annotations

from chunking_testkit import (
    children_of,
    chunk,
    context,
    document,
    heading,
    levels,
    list_node,
    paragraph,
    parent_ids,
    table,
)

from app.core.config import ChunkingSettings
from app.domain.models.ingestion.chunks import ChunkLevel

LONG_WORD = "x" * 400  # ~100 tokens per paragraph


class TestParentBoundaries:
    def test_sections_never_mix_across_headings(self) -> None:
        tree = document(
            [
                heading("Alpha", order=0),
                paragraph("Alpha body one.", order=1, heading_path=("Alpha",)),
                heading("Beta", level=1, order=2),
                paragraph("Beta body one.", order=3, heading_path=("Beta",)),
            ]
        )
        result = chunk(tree)
        assert len(result.parents) == 2
        alpha = result.parents[0]
        beta = result.parents[1]
        assert alpha.heading_path == ("Alpha",)
        assert beta.heading_path == ("Beta",)
        assert "Beta body" not in alpha.raw_text
        assert "Alpha body" not in beta.raw_text

    def test_preamble_without_heading_is_its_own_parent(self) -> None:
        tree = document(
            [
                paragraph("Intro text before any heading.", order=0),
                heading("Main", order=1),
                paragraph("Main body.", order=2, heading_path=("Main",)),
            ]
        )
        result = chunk(tree)
        assert len(result.parents) == 2
        assert result.parents[0].heading_path == ()
        assert result.parents[1].heading_path == ("Main",)

    def test_oversized_section_splits_at_paragraph_groups(self) -> None:
        settings = ChunkingSettings(parent_target_tokens=300, parent_hard_max_tokens=400)
        nodes: list = [heading("Long", order=0)]
        for i in range(12):
            nodes.append(paragraph(LONG_WORD, order=i + 1, heading_path=("Long",)))
        tree = document(nodes)
        result = chunk(tree, settings=settings)
        assert len(result.parents) >= 2
        assert all(p.token_count <= settings.parent_hard_max_tokens for p in result.parents)
        assert all(p.heading_path == ("Long",) for p in result.parents)


class TestChildContainment:
    def test_child_never_crosses_parent(self) -> None:
        tree = document(
            [
                heading("One", order=0),
                paragraph("First section content.", order=1, heading_path=("One",)),
                heading("Two", order=2),
                paragraph("Second section content.", order=3, heading_path=("Two",)),
            ]
        )
        result = chunk(tree)
        for child in result.children:
            owner = next(p for p in result.parents if p.id == child.parent_id)
            child_core = child.raw_text.replace("\n\n", "\n")
            assert all(
                line in owner.raw_text or line.strip() in owner.raw_text
                for line in child_core.splitlines()
                if line.strip()
            )

    def test_children_inherit_heading_path_and_anchors(self) -> None:
        tree = document(
            [
                heading("Deep", level=2, order=0),
                paragraph(
                    "Body under deep heading with enough text.",
                    order=1,
                    heading_path=("Deep",),
                    page=4,
                ),
            ]
        )
        result = chunk(tree)
        child = result.children[0]
        assert child.heading_path == ("Deep",)
        assert child.page_start == 4 and child.page_end == 4


class TestDeterministicIds:
    def test_same_input_same_ids(self) -> None:
        tree = document(
            [
                heading("S", order=0),
                paragraph("Deterministic body.", order=1, heading_path=("S",)),
            ]
        )
        first = chunk(tree)
        second = chunk(tree)
        assert parent_ids(first.parents) == parent_ids(second.parents)
        assert [c.id for c in first.children] == [c.id for c in second.children]

    def test_content_change_changes_child_id(self) -> None:
        def build(body_word: str) -> str:
            tree = document(
                [
                    heading("S", order=0),
                    paragraph(f"Body says {body_word}.", order=1, heading_path=("S",)),
                ]
            )
            return chunk(tree).children[0].id

        assert build("alpha") != build("beta")

    def test_ids_are_prefixed_hex(self) -> None:
        tree = document([paragraph("solo content here", order=0)])
        result = chunk(tree)
        assert result.parents[0].id.startswith("par-")
        assert len(result.parents[0].id) == 36
        assert result.children[0].id.startswith("chl-")


class TestSmallNodeMerging:
    def test_tiny_adjacent_paragraphs_merge_within_parent(self) -> None:
        tree = document(
            [
                heading("Notes", order=0),
                paragraph("tiny one", order=1, heading_path=("Notes",)),
                paragraph("tiny two", order=2, heading_path=("Notes",)),
            ]
        )
        settings = ChunkingSettings(merge_small_nodes_below_tokens=40)
        result = chunk(tree, settings=settings)
        section_children = children_of(result.children, result.parents[-1])
        assert len(section_children) == 1
        assert "tiny one" in section_children[0].raw_text
        assert "tiny two" in section_children[0].raw_text


class TestSplittingLadder:
    def test_single_giant_node_sentence_fallback(self) -> None:
        giant_paragraph = (
            ". ".join(f"Sentence number {i} talks about topic {i % 7}" for i in range(120)) + "."
        )
        tree = document([paragraph(giant_paragraph, order=0)])
        settings = ChunkingSettings(child_target_tokens=80, child_hard_max_tokens=120)
        result = chunk(tree, settings=settings)
        assert len(result.children) > 1
        assert all(c.token_count <= settings.child_hard_max_tokens for c in result.children)
        indexes = [c.chunk_index for c in result.children]
        assert indexes == list(range(len(indexes)))

    def test_unbreakable_string_token_cut_last_resort(self) -> None:
        unbroken = "z" * 8000
        tree = document([paragraph(unbroken, order=0)])
        settings = ChunkingSettings(child_target_tokens=200, child_hard_max_tokens=400)
        result = chunk(tree, settings=settings)
        assert len(result.children) >= 2
        assert all(c.token_count <= settings.child_hard_max_tokens + 1 for c in result.children)


class TestTableStrategy:
    def small_table_markdown(self) -> str:
        return "| A | B |\n| --- | --- |\n| 1 | 2 |"

    def large_table_markdown(self, rows: int = 60) -> str:
        header = "| Key | Value |"
        separator = "| --- | --- |"
        body = [f"| k{i} | v{i} padding padding padding |" for i in range(rows)]
        return "\n".join([header, separator, *body])

    def test_small_table_is_single_table_child(self) -> None:
        tree = document(
            [
                heading("Data", order=0),
                table(self.small_table_markdown(), order=1, heading_path=("Data",)),
                paragraph("Trailing explanation sentence.", order=2, heading_path=("Data",)),
            ]
        )
        result = chunk(tree)
        data_children = children_of(result.children, result.parents[-1])
        assert ChunkLevel.TABLE_CHILD in levels(data_children)
        table_children = [c for c in data_children if c.level is ChunkLevel.TABLE_CHILD]
        assert len(table_children) == 1
        assert "| 1 | 2 |" in table_children[0].raw_text
        assert table_children[0].parent_id == result.parents[-1].id

    def test_large_table_row_groups_repeat_header(self) -> None:
        tree = document(
            [
                heading("BigData", order=0),
                table(
                    self.large_table_markdown(), order=1, heading_path=("BigData",), rows=60, cols=2
                ),
            ]
        )
        settings = ChunkingSettings(child_target_tokens=80, child_hard_max_tokens=120)
        result = chunk(tree, settings=settings)
        table_children = [c for c in result.children if c.level is ChunkLevel.TABLE_CHILD]
        assert len(table_children) >= 2
        for child in table_children:
            lines = child.raw_text.splitlines()
            assert lines[0] == "| Key | Value |"
            assert lines[1] == "| --- | --- |"
        # rows are partitioned, not duplicated
        keys = [
            line.split("|")[1].strip()
            for child in table_children
            for line in child.raw_text.splitlines()[2:]
        ]
        assert len(keys) == len(set(keys))

    def test_table_metadata_preserved_on_node(self) -> None:
        node = table(self.small_table_markdown(), rows=1, cols=2)
        assert node.row_count == 1 and node.column_count == 2


class TestRawVsEmbeddingText:
    def test_embedding_text_carries_section_prefix_only(self) -> None:
        tree = document(
            [
                heading("RAG", order=0),
                paragraph(
                    "Hybrid retrieval combines dense and sparse retrieval.",
                    order=1,
                    heading_path=("RAG",),
                ),
            ]
        )
        result = chunk(tree)
        child = result.children[0]
        assert child.embedding_text.startswith("Section: RAG\n\n")
        assert not child.raw_text.startswith("Section:")
        assert child.raw_text in child.embedding_text

    def test_no_prefix_when_no_heading(self) -> None:
        tree = document([paragraph("Preamble only document text.", order=0)])
        result = chunk(tree)
        assert result.children[0].embedding_text == result.children[0].raw_text


class TestNormalizationAndOverlap:
    def test_whitespace_normalized_but_structure_kept(self) -> None:
        tree = document(
            [
                heading("T", order=0),
                paragraph("para   one\n\n\n\npara two", order=1, heading_path=("T",)),
                list_node(("bullet a", "bullet b"), order=2, heading_path=("T",)),
            ]
        )
        result = chunk(tree)
        parent = result.parents[-1]
        assert "\n\n\n" not in parent.raw_text
        assert "- bullet a" in parent.raw_text or "bullet a" in parent.raw_text

    def test_no_overlap_emitted_in_v1(self) -> None:
        tree = document(
            [
                paragraph("First paragraph about retrieval quality metrics.", order=0),
                paragraph("Second paragraph about reranking models.", order=1),
            ]
        )
        result = chunk(tree)
        texts = [c.raw_text for c in result.children]
        for i in range(len(texts)):
            for j in range(len(texts)):
                if i != j:
                    assert texts[i] not in texts[j]


class TestMetadataContracts:
    def test_parent_contract_fields(self) -> None:
        tree = document(
            [heading("M", order=0), paragraph("Contract body.", order=1, heading_path=("M",))]
        )
        result = chunk(tree)
        parent = result.parents[-1]
        assert parent.document_id and parent.document_version_id
        assert parent.source_block_ids
        assert len(parent.content_hash) == 64
        assert parent.parent_chunker_version

    def test_child_contract_fields(self) -> None:
        ctx = context(embedding_model="AITeamVN/Vietnamese_Embedding")
        tree = document([paragraph("Child contract body text.", order=0)])
        result = chunk(tree, ctx)
        child = result.children[0]
        assert child.filename == "doc.pdf"
        assert child.mime_type == "application/pdf"
        assert child.source_type == "upload"
        assert child.embedding_model == "AITeamVN/Vietnamese_Embedding"
        assert child.child_chunker_version


class TestReviewM1TokenCalibration:
    def test_structural_markdown_priced_higher_than_prose(self) -> None:
        from app.services.ingestion.chunking.tokens import estimate_tokens

        prose = "tu" * 100  # 200 chars of plain text
        tableish = "| a | b |\n" * 20  # same-ish length, heavy pipes
        assert estimate_tokens(tableish) > estimate_tokens(prose)

    def test_vietnamese_prose_estimate_is_conservative(self) -> None:
        """Estimate >= real XLM-R count measured in the P09C benchmark."""
        from app.services.ingestion.chunking.tokens import estimate_tokens

        vi_text = (
            "Xác nhận tag release đã tồn tại và CI xanh trước khi bắt đầu. "
            "Áp các migration bằng tài khoản deploy đã khóa."
        )
        # Real tokenizer count for this sample is ~33; estimator must not be lower.
        assert estimate_tokens(vi_text) >= 30


class TestReviewM2ProvenanceFidelity:
    def test_children_anchor_to_their_own_segments(self) -> None:
        settings = ChunkingSettings(merge_small_nodes_below_tokens=0)
        tree = document(
            [
                heading("S", order=0),
                paragraph(
                    "First paragraph lives on page two.", order=1, heading_path=("S",), page=2
                ),
                paragraph(
                    "Second paragraph lives on page five.", order=2, heading_path=("S",), page=5
                ),
            ]
        )
        result = chunk(tree, settings=settings)
        section_parent = result.parents[-1]
        kids = children_of(result.children, section_parent)
        pages = [(c.page_start, c.page_end) for c in kids]
        assert (2, 2) in pages and (5, 5) in pages
        per_child_blocks = [c.source_block_ids for c in kids]
        assert any("n0001" in blocks for blocks in per_child_blocks)
        assert any("n0002" in blocks for blocks in per_child_blocks)

    def test_merged_children_union_their_anchors(self) -> None:
        tree = document(
            [
                heading("S", order=0),
                paragraph(
                    "First paragraph lives on page two.", order=1, heading_path=("S",), page=2
                ),
                paragraph(
                    "Second paragraph lives on page five.", order=2, heading_path=("S",), page=5
                ),
            ]
        )
        result = chunk(tree)  # default merging: tiny paragraphs combine
        merged = result.children[-1]
        assert set(merged.source_block_ids) == {"n0001", "n0002"}
        assert (merged.page_start, merged.page_end) == (2, 5)


class TestReviewL1MalformedTable:
    def test_separator_first_line_stays_single_unit(self) -> None:
        markdown = "| --- | --- |\n| orphan | row |"
        tree = document([table(markdown, rows=1, cols=2)])
        result = chunk(tree)
        table_children = [c for c in result.children if c.level is ChunkLevel.TABLE_CHILD]
        assert len(table_children) == 1
        assert "| orphan | row |" in table_children[0].raw_text


class TestReviewL2OversizedRowAtomic:
    def test_oversized_row_never_split(self) -> None:
        wide_row = f"| k | {'v' * 600} |"
        markdown = "| Key | Value |\n| --- | --- |\n" + wide_row + "\n| small | cell |"
        tree = document([table(markdown, rows=2, cols=2)])
        settings = ChunkingSettings(child_target_tokens=40, child_hard_max_tokens=80)
        result = chunk(tree, settings=settings)
        table_children = [c for c in result.children if c.level is ChunkLevel.TABLE_CHILD]
        assert len(table_children) >= 2
        joined = "\n".join(c.raw_text for c in table_children)
        # the wide row appears exactly once, unbroken
        assert joined.count("| k |") == 1
        assert wide_row.split("|")[2].strip()[:50] in joined


class TestReviewL3HardCutLossless:
    def test_cut_pieces_reconstruct_source(self) -> None:
        from app.services.ingestion.chunking.tokens import (
            estimate_tokens,
            truncate_at_token_boundary,
        )

        original = "".join(f"w{i} " for i in range(200)).strip()
        remaining = original
        pieces: list[str] = []
        while remaining and estimate_tokens(remaining) > 50:
            cut, consumed = truncate_at_token_boundary(remaining, 40)
            assert consumed <= len(remaining)
            pieces.append(cut.strip())
            remaining = remaining[consumed:].strip()
        if remaining:
            pieces.append(remaining)
        # Hard cuts may split words; the guarantee is character-level
        # losslessness — no duplication, no loss (review L3).
        assert sorted("".join(pieces).replace(" ", "")) == sorted(original.replace(" ", ""))


class TestReviewL4FilenameEnforced:
    def test_child_draft_requires_filename(self) -> None:
        import pytest as _pytest
        from pydantic import ValidationError as _VE

        from app.domain.models.ingestion.chunks import ChildChunkDraft

        def draft_with(filename: str) -> None:
            ChildChunkDraft(
                id="chl-" + "a" * 32,
                document_id="d",
                document_version_id="v",
                source_id="s",
                title="t",
                raw_text="body",
                token_count=1,
                content_hash="ab" * 32,
                parent_id="par-" + "b" * 32,
                chunk_index=0,
                embedding_text="Section: t\n\nbody",
                child_chunker_version="p9c-child-1.0.0",
                filename=filename,
            )

        with _pytest.raises(_VE):
            draft_with("")


class TestReviewL5HeadingContextInParent:
    def test_parent_raw_keeps_section_heading_line(self) -> None:
        tree = document(
            [
                heading("Deployment", order=0),
                paragraph(
                    "Body paragraph about deployment steps.",
                    order=1,
                    heading_path=("Deployment",),
                ),
            ]
        )
        result = chunk(tree)
        parent = result.parents[-1]
        assert parent.raw_text.startswith("Deployment\n\n")
        # heading line is context only — it never becomes its own child
        child_texts = [c.raw_text for c in result.children]
        assert all(text != "Deployment" for text in child_texts)


class TestChunkingSettingsBounds:
    def test_hard_max_below_target_is_rejected(self) -> None:
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="child_hard_max_tokens must be"):
            ChunkingSettings(child_target_tokens=500, child_hard_max_tokens=20)
        with pytest.raises(ValidationError, match="parent_hard_max_tokens must be"):
            ChunkingSettings(parent_target_tokens=1600, parent_hard_max_tokens=100)

    def test_zero_and_negative_targets_are_rejected(self) -> None:
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ChunkingSettings(child_target_tokens=0, child_hard_max_tokens=20)
        with pytest.raises(ValidationError):
            ChunkingSettings(parent_target_tokens=-50, parent_hard_max_tokens=-1)

    def test_merge_below_cannot_exceed_hard_max(self) -> None:
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="merge_small_nodes_below_tokens"):
            ChunkingSettings(merge_small_nodes_below_tokens=900)
