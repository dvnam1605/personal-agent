# Agentic RAG tổng quát — tiến độ và việc tiếp theo

Bản kế hoạch đầy đủ đã duyệt: `C:\Users\dauva\.claude\plans\thi-t-k-plan-cho-reflective-tower.md`.
File này ghi lại **đã làm gì** và **làm tiếp từ đâu**.

- **Nhánh làm việc:** `feat/generic-rag-profiles`, tách từ `fix/rag-hardening`. Cả hai nhánh đều chưa merge vào `main`.
- **Cập nhật lần cuối:** 2026-10-20.

## Mục tiêu

RAG dùng được cho **mọi loại tài liệu**: hành chính, báo cáo/tờ trình, hợp đồng/biên bản, kỹ thuật/hướng dẫn, đồ án/luận văn, Excel.

- Câu hỏi về **con số hoặc danh sách** (đếm, liệt kê, mới nhất) lấy kết quả từ SQL trên metadata.
- Câu hỏi về **nội dung** lấy từ RAG.
- Câu trả lời có trích dẫn thật, biết từ chối khi thiếu bằng chứng, truy vết được, và đo được bằng bộ gold set.
- Có thêm tính năng **tạo tài liệu theo yêu cầu** (P5): dàn ý → người dùng duyệt → viết → xuất .docx và Markdown.

## Trạng thái

| Bước | Nội dung | Trạng thái |
|---|---|---|
| Hardening | Sửa SQL khi query có dấu `:`, chỉ 1 lần gọi LLM viết lại câu hỏi, `ef_search`/iterative scan, FTS fallback theo bigram, boundary chống injection, deadline cho synthesis | ✅ `ac418b4` (nhánh `fix/rag-hardening`) |
| P1.1 | Migration `0011` (`document_profiles`), package `profiling` (8 loại tài liệu, rule trước → LLM khi không chắc), nối vào ingestion, backfill chỉ metadata | ✅ `cb5343a`, `3dfe59a` |
| P1.2 | DB thật lên `0011`; backfill 39 profile; ingest file mẫu mới; cờ review khi số hiệu trong văn bản khác tên file; sửa bảng docling rỗng; tiêu đề/nhãn cho PDF | ✅ `4e21e53`, `a1bec0f`, `6555db7`, `d8eb51b` |
| P1.3 | Route metadata đếm/liệt kê/mới nhất: planner bằng rule, SQL có bind params, câu trả lời xác định + trích dẫn cấp tài liệu; RAG giới hạn trong tài liệu được chọn | ✅ `71864ce` |
| P1.4 | Gỡ hard-code VOV/pháp lý: prompt, condenser, catalog đọc từ profile, header bằng chứng có loại/số/ngày/phiên bản, tiêu đề trích dẫn từ profile | ✅ `3b07ad9` |
| P2a | Nhãn `[E#]`, chỉ trích dẫn thật (bỏ top-3 tự gắn), đánh số chung cho stream và non-stream, sửa nhãn sufficiency ở frontend | ✅ `c85c7fd` |
| P2b | Grounding: từ chối khi số hiệu/mã hỏi không có trong bằng chứng; câu có số liệu mà không trích dẫn → `PARTIAL` | ✅ `e551d28` |
| P2c | Migration `0012` (`run_evidence`), lưu snapshot bằng chứng phía server, `/turn` dựng lại citation từ server, `GET /runs/{id}/evidence` | ✅ `d0bcc74` (DB thật đã ở `0012`) |
| Sửa thêm | LLM fail nhanh khi 9router không chạy (connect 10s, không POST lại); số hiệu khớp bất kể dấu phân cách (`80/QĐ` = `80-QĐ`) | ✅ `8332aaf`, `82a3eea` |
| P5 | Tạo tài liệu: dàn ý → duyệt → viết từng mục → .docx + Markdown | ⏭ **làm tiếp từ đây** |
| P3 | Gold set + runner đánh giá (gồm cả nhóm generation) | ⬜ |
| P4 | Excel → bảng SQL + text-to-SQL an toàn | ⬜ |

**Test:** toàn bộ unit test chạy ổn (khoảng 1150 test). Hai test đỏ đã có từ trước và không liên quan:
- `test_config::test_timeouts_settings` (kỳ vọng 30s, cấu hình hiện là 90s);
- `test_health_routes` (phụ thuộc kết nối live, đôi khi đỏ).

## Kết quả kiểm tra end-to-end (API thật, 2026-10-20)

| Câu hỏi | Kết quả |
|---|---|
| "Có bao nhiêu quyết định năm 2025?" | **9**, danh sách có số hiệu, ngày và trích dẫn tài liệu (khớp với SQL đếm tay) |
| "Có bao nhiêu đồ án?" | **1** |
| "Quyết định 80/QĐ-TNVN quy định gì?" | RAG giới hạn đúng 1 tài liệu (văn bản in `80-QĐ/TNVN`). `run_evidence` ghi 2 bằng chứng kèm trace. |
| "Định mức chi hội nghị là bao nhiêu tiền?" | Đi RAG nội dung, không bị hiểu nhầm là câu đếm |

**9router đang không chạy**, kể cả từ trong container (`host.docker.internal:20128` connect timeout). Vì vậy:
- các câu cần LLM nhận câu trả lời dự phòng (danh sách tài liệu liên quan, `PARTIAL`);
- chưa đo được chất lượng câu trả lời có trích dẫn `[E#]` trên LLM thật.

## Dữ liệu hiện có trong DB

| Tài liệu | Trạng thái |
|---|---|
| 37 quyết định/chỉ thị | 3 profile cần review: 2 thiếu số hiệu hoặc độ tin cậy thấp; file `4351QD` in số `4357` (nghi lỗi OCR) |
| `BaoCao/Bao_Cao_Tuan…docx` | Đã ingest; loại `report` |
| `DoAn/DoAn-DauVanNam…pdf` | Đã ingest (111 parent, 312 child); loại `academic`. Chưa có tiêu đề (`missing:title`), chờ LLM khi 9router chạy lại. |
| `DoAn/Nguyễn Tuấn Linh…pdf` (slide) | Lần đầu lỗi do bảng rỗng (đã sửa ở `a1bec0f`); **cần chạy lại** |
| `TaiLieuKyThuat/dibia…pdf` (sách 399 trang) | Đang embed trên CPU tại thời điểm cập nhật (parse mất khoảng 20 phút) |

Lệnh ingest lại file slide:
```
uv run python scripts/ingest_corpus.py --root data/DoAn --no-llm-profile
```

## P5 — tạo tài liệu (việc tiếp theo)

Thiết kế chi tiết nằm trong plan. Các điểm chính:
- Intent `document_generation` trong triage.
- Registry template theo loại tài liệu.
- `DocumentPlanner` sinh dàn ý; dàn ý được duyệt qua cơ chế interrupt/question đã có.
- `SectionWriter` viết từng mục bằng RAG `DOCUMENT_SEARCH` có nhãn `[E#]`; bằng chứng ghi vào `run_evidence`.
- `DocxRenderer` dùng python-docx; tải file qua `GET /documents/generated/{id}` có kiểm tra quyền sở hữu.

## Lưu ý khi làm tiếp

- **WSL tự tắt** khi không còn tiến trình nào, kéo theo Docker/Postgres. Khi chạy script dài, giữ WSL sống bằng một tiến trình nền, ví dụ `wsl -e sh -c 'sleep 7200'`.
- **Backup DB:** `~/backups/assistant_pre_0011_*.dump` và `~/backups/assistant_pre_0012_*.dump` trong WSL.
- **Backend container** mount `./app` với `--reload`. Khi thêm router hoặc migration, nên `docker restart assistant_backend`.
- **9router từ Windows:** `ROUTER_BASE_URL` trong `.env` trỏ `host.docker.internal`, Windows không phân giải được. Khi chạy script ngoài Docker cần dùng `localhost:20128`.
- `app/agents/declarations.py` đang có thay đổi **không phải do Claude làm**; chưa commit.
- Khi viết code có `\n` trong chuỗi, không ghép qua heredoc Python trong Bash; dùng Edit/Write trực tiếp.
