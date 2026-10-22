"""Images of cited evidence go under the answer lines they belong to."""

from __future__ import annotations

import uuid

from app.core.images import for_prompt, image_ref
from app.domain.models.retrieval import Evidence, EvidenceBundle
from app.services.retrieval.images import interleave, refs_in, restore_images, split_images
from app.services.retrieval.synthesis import PromptAnswerSynthesizer

KEY_A = "a" * 24
KEY_B = "b" * 24
KEY_C = "c" * 24
KEY_D = "d" * 24
GUIDE = {
    1: [
        ("Hình 1 — mô tả tự động: tệp Dism++x64 trong File Explorer", KEY_A, "chọn Dism++x64."),
        ("Hình 2 — mô tả tự động: cửa sổ User Agreement", KEY_B, "Tiếp tục chọn Accept."),
        ("Hình 3 — mô tả tự động: hộp thoại Tips, nút Cancel", KEY_C, "Tiếp tục chọn Cancel."),
        ("Hình 4 — mô tả tự động: thư mục ActiveBackup", KEY_D, "Lưu vào Google Drive."),
    ]
}


def guide(number: int) -> list[tuple[str, str, str]]:
    return GUIDE.get(number, [])


def ref(index: int) -> str:
    alt, key, _ = GUIDE[1][index]
    return image_ref(alt, key)


def test_model_sees_only_the_description() -> None:
    text = "Tiếp tục chọn Accept.\n\n" + image_ref("Hình 1 — mô tả tự động: hộp thoại", KEY_A)
    assert for_prompt(text) == "Tiếp tục chọn Accept.\n\n(Hình 1 — mô tả tự động: hộp thoại)"


def test_screenshots_go_under_their_steps_not_under_the_intro() -> None:
    answer = (
        "Theo hướng dẫn sao lưu bản quyền bằng Dism++ [1], các bước như sau:\n"
        "1. Mở Dism++x64 nếu máy 64-bit [1].\n"
        "2. Tại cửa sổ User Agreement, bấm Accept [1].\n"
        "3. Nếu hiện thông báo cập nhật, bấm Cancel [1].\n"
        "4. Chọn OK để kết thúc [1]."
    )
    lines = interleave(answer, guide).split("\n")
    assert lines[1:7] == [
        "1. Mở Dism++x64 nếu máy 64-bit [1].",
        ref(0),
        "2. Tại cửa sổ User Agreement, bấm Accept [1].",
        ref(1),
        "3. Nếu hiện thông báo cập nhật, bấm Cancel [1].",
        ref(2),
    ]
    # Hình 4 matched no step and comes after the last match: left out.
    assert lines[7:] == ["4. Chọn OK để kết thúc [1]."]
    assert KEY_D not in "\n".join(lines)


def test_an_unmatched_step_between_two_matches_is_kept() -> None:
    answer = "1. Mở Dism++x64 [1].\n2. Bước giữa [1].\n3. Bấm Cancel trên hộp thoại Tips [1]."
    out = interleave(answer, guide)
    assert out.index(KEY_A) < out.index(KEY_B) < out.index(KEY_C)  # Hình 2 rides with Hình 3


def test_sharing_the_topic_words_is_not_enough() -> None:
    # "sao lưu" is the guide's topic: two words of a source sentence decide nothing.
    source = "1: Sao lưu bản quyền. Mở Dism++ trên máy 64bit.\n\n" + image_ref("Hình 1", KEY_A)
    images = {1: refs_in(source)}
    answer = "Dism++ không sao lưu được bản quyền Photoshop [1]."
    assert interleave(answer, lambda n: images.get(n, [])) == answer


def test_an_answer_about_none_of_the_images_shows_none() -> None:
    refusal = "Tài liệu hướng dẫn sao lưu [1] không nêu lệnh kiểm tra Office 2016."
    assert interleave(refusal, guide) == refusal


def test_each_image_once_and_uncited_evidence_shows_none() -> None:
    images = {1: GUIDE[1][:1], 2: GUIDE[1][1:2]}
    out = interleave("Mở Dism++x64 [1].\n\nLại Dism++x64 [1].", lambda n: images.get(n, []))
    assert out.count(KEY_A) == 1 and KEY_B not in out
    assert interleave("Không trích dẫn.", guide) == "Không trích dẫn."


def test_images_survive_a_rewrite_of_the_prose() -> None:
    written = interleave("1. Mở Dism++x64 [1].\n2. Bấm Accept [1].", guide)
    prose, images = split_images(written)
    assert "image:" not in prose
    # A gate or review rewords a step; its screenshots stay under it.
    again = restore_images(prose.replace("Mở", "Khởi chạy"), images)
    assert again == written.replace("Mở", "Khởi chạy")


def test_images_of_a_removed_line_stay_in_order() -> None:
    written = interleave("1. Mở Dism++x64 [1].\n2. Bấm Accept ở User Agreement [1].", guide)
    prose, images = split_images(written)
    again = restore_images(prose.replace("1. Mở Dism++x64 [1].\n", ""), images)
    assert again.index(KEY_A) < again.index(KEY_B)


def _bundle() -> EvidenceBundle:
    def evidence(content: str) -> Evidence:
        return Evidence(
            evidence_id=uuid.uuid4().hex,
            kind="CHILD",
            content_raw=content,
            token_estimate=10,
            primary_chunk_id=uuid.uuid4().hex[:8],
            document_id="doc-1",
        )

    step = evidence(
        "Tiếp tục chọn Toolkit.\n\n" + image_ref("Hình 4 — mô tả tự động: mục Toolkit", KEY_A)
    )
    other = evidence("Giới thiệu chung.\n\n" + image_ref("Hình 1", KEY_B))
    return EvidenceBundle(
        items=[other, step],
        total_tokens=20,
        documents_used=["doc-1"],
        parent_ids_used=[],
        retrieval_trace_id=uuid.uuid4().hex,
    )


ANSWER = "Mở Dism++ rồi chọn Toolkit [E2].\n[Hình 4]\n[Hình 9]\n\nSau đó chọn Activation Backup."
EXPECTED = (
    "Mở Dism++ rồi chọn Toolkit [1].\n"
    f"![Hình 4 — mô tả tự động: mục Toolkit](image:{KEY_A})\n\n"
    "Sau đó chọn Activation Backup."
)


async def test_the_model_places_the_screenshot_and_invented_ones_vanish() -> None:
    prompts: list[str] = []

    async def gen(system: str, user: str) -> str:
        prompts.append(user)
        return ANSWER

    result = await PromptAnswerSynthesizer(generate=gen).synthesize("Làm sao?", _bundle())
    assert "image:" not in prompts[0] and "(Hình 4 — mô tả tự động: mục Toolkit)" in prompts[0]
    assert result.answer == EXPECTED  # [Hình 9] is in no evidence


async def test_streamed_markers_become_images_as_they_arrive() -> None:
    async def stream(system: str, user: str):
        for piece in (ANSWER[:38], ANSWER[38:]):  # the marker is split across chunks
            yield piece

    synthesizer = PromptAnswerSynthesizer(generate=None, stream_generate=stream)
    events = [e async for e in synthesizer.synthesize_stream("Làm sao?", _bundle())]
    tokens = "".join(e["delta"] for e in events if e["type"] == "token")
    final = next(e for e in events if e["type"] == "citations")
    assert f"![Hình 4 — mô tả tự động: mục Toolkit](image:{KEY_A})" in tokens
    assert "\n[Hình" not in tokens and "answer" not in final  # no bare marker left


async def test_an_answer_that_is_not_a_procedure_gets_no_images() -> None:
    async def gen(system: str, user: str) -> str:
        return "Dism++ không sao lưu được bản quyền Photoshop, chỉ hỗ trợ Toolkit [E2]."

    result = await PromptAnswerSynthesizer(generate=gen).synthesize("Có không?", _bundle())
    assert "image:" not in result.answer


async def test_steps_left_without_markers_still_get_their_screenshots() -> None:
    async def gen(system: str, user: str) -> str:
        return "1. Mở Dism++ [E2].\n2. Chọn mục Toolkit [E2].\n3. Chọn Activation Backup [E2]."

    result = await PromptAnswerSynthesizer(generate=gen).synthesize("Làm sao?", _bundle())
    assert f"2. Chọn mục Toolkit [1].\n![Hình 4 — mô tả tự động: mục Toolkit](image:{KEY_A})" in (
        result.answer
    )


def test_an_uncited_step_inside_the_list_can_host_its_screenshot() -> None:
    answer = (
        "1. Tại cửa sổ User Agreement, bấm Accept [1].\n"
        "2. Nếu hiện hộp thoại Tips, bấm Cancel.\n"
        "3. Kết thúc [1]."
    )
    lines = interleave(answer, guide).split("\n")
    assert lines[3] == ref(2)  # under step 2, which cites nothing
