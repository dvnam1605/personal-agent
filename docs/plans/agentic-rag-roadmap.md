# Agentic RAG tổng quát — tiến độ và việc tiếp theo

Bản kế hoạch đầy đủ đã duyệt: `C:\Users\dauva\.claude\plans\thi-t-k-plan-cho-reflective-tower.md`.
File này ghi lại **đã làm gì** và **làm tiếp từ đâu**.

- **Nhánh làm việc:** `feat/generic-rag-profiles`, tách từ `fix/rag-hardening`. Cả hai nhánh đều chưa merge vào `main`.
- **Cập nhật lần cuối:** 2026-10-19.

## Mục tiêu

RAG dùng được cho **mọi loại tài liệu**: hành chính, báo cáo/tờ trình, hợp đồng/biên bản, kỹ thuật/hướng dẫn, đồ án/luận văn, Excel.

- Câu hỏi về **con số hoặc danh sách** (đếm, liệt kê, mới nhất) lấy kết quả từ SQL trên metadata.
- Câu hỏi về **nội dung** lấy từ RAG.
- Câu trả lời có trích dẫn thật, biết từ chối khi thiếu bằng chứng, truy vết được, và đo được bằng bộ gold set.
- Có thêm tính năng **tạo tài liệu theo yêu cầu** (P5): dàn ý → người dùng duyệt → viết → xuất .docx và Markdown.

## Trạng thái

| Bước | Nội dung | Trạng thái |
|---|---|---|
| Hardening | Sửa SQL khi query có dấu `:`, chỉ 1 lần gọi LLM viết lại câu hỏi, `ef_search`/iterative scan, FTS fallback theo bigram, boundary chống injection, deadline cho synthesis | ✅ commit `ac418b4` (nhánh `fix/rag-hardening`) |
| **P1.1a** | Migration `0011` (bảng `document_profiles`, JSON→JSONB), model `DocumentProfile`, package `app/services/ingestion/profiling/` (registry loại, rule classifier, rules chung, LLM fallback, overrides), 22 unit test | ✅ commit `cb5343a` |
| **P1.1b** | Nối profiler vào `IngestionOrchestrator`, `SqlAlchemyProfileRepository`, backfill chỉ metadata (`scripts/backfill_profiles.py`), `ingest_corpus.py` nhận mọi định dạng, sửa lỗi docling nối text bằng dấu cách, parser không gọi LLM nữa | ✅ commit `3dfe59a` |
| P1.2 | Chạy migration + backfill trên DB thật, ingest 4 file mẫu mới | ⏭ **làm tiếp từ đây** |
| P1.3 | Route metadata: đếm, liệt kê, lọc, mới nhất | ⬜ |
| P1.4 | Gỡ hard-code VOV/pháp lý (prompt, catalog, condenser, triage) | ⬜ |
| P2 | Trích dẫn thật `[E#]`, từ chối có cấu trúc, bảng `run_evidence`, xử lý phiên bản/mâu thuẫn | ⬜ |
| P5 | Tạo tài liệu: dàn ý → duyệt → viết từng mục → .docx + Markdown | ⬜ |
| P3 | Gold set + runner đánh giá (gồm cả nhóm generation) | ⬜ |
| P4 | Excel → bảng SQL + text-to-SQL an toàn | ⬜ |

**Test:** 392 test thuộc ingestion, infrastructure, scripts và retrieval đều pass. Lint không thêm lỗi mới; các lỗi BLE001 còn lại đã có từ trước.

## Kết quả profiler (chạy thử chỉ bằng rule, chưa ghi vào DB)

| File | Loại | Kết quả |
|---|---|---|
| Quyết định/Chỉ thị VOV | administrative | Số hiệu, ngày, cơ quan, người ký đều đúng. Tiêu đề dạng "Quyết định về việc …". File không có số hiệu → `needs_review`. |
| `BaoCao/Bao_Cao_Tuan…docx` | report | Tác giả, ngày 16/10/2026, đơn vị "AI" |
| `DoAn/DoAn-DauVanNam…pdf` | academic | Đúng tên đề tài, GVHD, tác giả, chương trình, khoa |
| `DoAn/Nguyễn Tuấn Linh…pdf` (slide) | academic | Đúng tiêu đề, MSSV, GVHD. Không có ngày. |
| `TaiLieuKyThuat/dibia…pdf` (sách tiếng Anh) | technical | `lang=en`, tác giả, ISBN, 11/2025 |

## P1.2 — các bước tiếp theo (chạy trên Windows, DB ở `localhost:5434`)

1. **Backup đã có sẵn:** `~/backups/assistant_pre_0011_20261019_1041.dump` trong WSL, tạo bằng `pg_dump -Fc` từ container `assistant_postgres`. DB hiện vẫn ở revision `0010`.
2. **Chạy migration:** `uv run alembic upgrade head`, kiểm tra ra `0011` và bảng `document_profiles`.
3. **Xem trước backfill:** `uv run python scripts/backfill_profiles.py --dry-run --no-llm`.
4. **Ghi profile:** `uv run python scripts/backfill_profiles.py --no-llm`, sau đó chạy thêm một lần không có `--no-llm` để LLM chỉ xử lý các profile bị `needs_review`.
5. **Kiểm tra embedding không đổi:** so `md5(string_agg(embedding::text, ',' ORDER BY id))` trước và sau.
6. **Ingest 4 file mẫu mới:** `uv run python scripts/ingest_corpus.py --root data --no-llm-profile`.
   - Các file trong `data/QuyetDinh` bị bỏ qua nhờ fingerprint (đã kiểm source_id khớp 37/37).
   - Sách 399 trang sẽ parse bằng docling khá lâu.
   - Nếu PDF bị báo `NEEDS_OCR` thì xem `settings.parsing`.
7. **Xem kết quả:** chạy `SELECT doc_category, count(*) FROM document_profiles p JOIN documents d ON d.id = p.document_id WHERE d.is_active GROUP BY 1`, và xem các profile `needs_review`.

## Việc tiếp theo sau P1.2 (tóm tắt, chi tiết trong plan)

- **P1.3 — route metadata:**
  - `METADATA_INTENT_PATTERN` trong `routing/triage_rules.py`.
  - `parameters["knowledge_intent"]` trong `routing/triage.py`.
  - `domain/models/retrieval/metadata_filter.py` (`MetadataFilterSpec`, `StructuredResult`).
  - Package `services/retrieval/metadata/` gồm `filter_parser`, `compiler` (SQLAlchemy Core + bind params + owner scope + `is_active`), `service`, `render`.
  - Nối vào `_handle_knowledge`/stream trong `routing/query/orchestrator.py`.
  - `LEFT JOIN document_profiles` trong `retrieval/sql.py` để header bằng chứng có thêm loại, số hiệu, ngày.
  - Có thể tái dùng `query_synonyms` trong `profiling/categories.py` cho bộ parse câu hỏi.
- **P1.4 — gỡ hard-code:**
  - `entity_catalog` đọc từ `document_profiles` thay vì `administrative_metadata`, cache theo owner, `invalidate()`.
  - Xóa 5 tên người trong `routing/query/orchestrator.py` (khoảng dòng 130–137) và phần viết lại "do {subject} ký".
  - Prompt chung, không còn VOV.
  - Ngừng ghi `administrative_metadata` vào chunk và gỡ `AdministrativeMetadataExtractor` khỏi parser.
- **P2, P5, P3, P4:** xem plan.

## Lưu ý khi làm tiếp

- `app/agents/declarations.py` đang có thay đổi **không phải do Claude làm**. Chưa commit, cần bạn tự xem lại.
- Từ Windows, `ROUTER_BASE_URL=http://host.docker.internal:20128/v1` không phân giải được. Khi chạy script ngoài Docker cần đổi sang `localhost:20128`, nếu không LLM fallback sẽ lỗi và profile chỉ được đánh dấu `needs_review`.
- `data/` nằm trong `.gitignore`. Bốn file mẫu mới chỉ có trên máy: `data/BaoCao/`, `data/DoAn/`, `data/TaiLieuKyThuat/`.
- Backend container (`assistant_backend`) đang chạy. Sau khi migrate nên restart để model `_JSONB` khớp với schema.
- Khi viết code có `\n` trong chuỗi, không ghép qua heredoc Python trong Bash vì hay hỏng escape; dùng Edit/Write trực tiếp.
