# Plan thử nghiệm: GraphRAG (Neo4j) chạy song song để so sánh

> Mục đích: trả lời bằng số liệu câu hỏi *"chuyển sang (hoặc thêm) graph có làm RAG tốt hơn rõ rệt không?"*.
> Đây là **spike có giới hạn thời gian** (1–2 ngày làm việc), không thay hệ hiện tại.
> Hệ so sánh: [ontogr/agentic-graphrag](https://github.com/ontogr/agentic-graphrag), dùng Neo4j, trích thực thể bằng GLiNER và kiểm tra trích dẫn.

---

## 0. Câu hỏi cần trả lời và tiêu chí quyết định

| # | Câu hỏi | Đo bằng |
|---|---|---|
| Q1 | Với **câu hỏi về quan hệ hoặc cần nhiều bước** (thay thế, căn cứ, ai khen ai, ai ký…), graph có trả lời đúng hơn không? | Bộ R1–R15 ở mục 6 |
| Q2 | Với **câu hỏi thường** (nội dung, đếm, liệt kê), graph có kém đi không? | Gold set hiện có (19 câu) |
| Q3 | Trích **thực thể và quan hệ từ văn bản hành chính tiếng Việt** có đủ tốt không? | Chấm tay 30 mẫu (mục 5) |
| Q4 | Chi phí vận hành thế nào? | RAM, thời gian ingest, số lần gọi LLM mỗi câu, độ trễ |

**Quyết định:**
- **Đưa graph vào** khi đạt cả ba điều sau:
  - nhóm R đúng hơn ít nhất **+25 điểm phần trăm**;
  - nhóm gold set **không kém quá 5 điểm phần trăm**;
  - chất lượng quan hệ trích ra **≥ 80% đúng**.
  
  Khi đó dựng lớp graph **bên trong Postgres** (bảng `entities` / `relations` truy vấn bằng CTE đệ quy, hoặc extension Apache AGE), không thêm Neo4j vào hệ chính.
- **Chỉ giữ ý tưởng** khi R tốt hơn nhưng chất lượng trích tiếng Việt dưới 80%. Khi đó chỉ trích quan hệ bằng rule + LLM cho vài loại quan hệ hành chính.
- **Bỏ** khi R không tốt hơn rõ rệt.

---

## 1. Tổng quan quy trình

```
Máy hiện tại (A)                         Máy thử nghiệm 192.168.1.191 (B)
──────────────────                       ─────────────────────────────────
A1 xuất corpus .md (đúng text đã index)  ─►  B4 nạp vào agentic-graphrag (Neo4j)
A2 chạy baseline hệ hiện tại             ─►  C  so sánh, viết REPORT.md
A3 chuẩn bị bộ câu hỏi R (mục 6)         ─►  B6 chạy cùng bộ câu hỏi trên graph
```

Có thể làm toàn bộ trên máy B nếu đã chép cả `data/`, `models/` và dump DB sang (mục 2.3).

---

## 2. Chuẩn bị

### 2.1 Yêu cầu máy B
- RAM ≥ 8 GB: Neo4j cần khoảng 1–1,5 GB, embedder và GLiNER chạy CPU khoảng 2 GB. Có GPU thì nhanh hơn nhưng không bắt buộc.
- Docker và Docker Compose, Python **≥ 3.11**, `uv`, `git`.
- Ổ trống ≥ 15 GB, gồm model, image Docker và dữ liệu Neo4j.
- **LLM:**
  - 9router chạy trên máy A (`localhost:20128`), mặc định chỉ nghe localhost;
  - **phương án 1:** trên máy A cho 9router nghe IP LAN, rồi từ máy B dùng `http://<IP máy A>:20128/v1`;
  - **phương án 2:** dùng key API riêng trên máy B (OpenAI-compatible).
  - Kiểm tra từ máy B:
    ```bash
    curl -s http://<host>:20128/v1/models -H "Authorization: Bearer $KEY" | head
    ```

### 2.2 Dữ liệu cần mang sang máy B
| Thứ | Lấy ở đâu | Bắt buộc |
|---|---|---|
| Code (repo, nhánh `feat/generic-rag-profiles`) | `git clone` hoặc scp | ✔ |
| Corpus `.md` đã parse | `scripts/ingestion/export_corpus_md.py` (mục 3.1) | ✔ |
| Bộ câu hỏi | `tests/evaluation/gold/rag_gold_v1.json` + mục 6 | ✔ |
| Model embedding tiếng Việt | `models/models--AITeamVN--Vietnamese_Embedding/` hoặc tải lại từ HF `AITeamVN/Vietnamese_Embedding` | ✔ |
| Dump DB (nếu muốn chạy cả hệ hiện tại trên B) | `pg_dump` (mục 2.3) | tuỳ chọn |
| `data/` gốc (PDF/DOCX/MD) | scp | tuỳ chọn |

### 2.3 (Tuỳ chọn) Chạy cả hệ hiện tại trên máy B
1. Trên máy A, khi `assistant_postgres` đang chạy:
   ```bash
   docker exec assistant_postgres sh -c 'pg_dump -U $POSTGRES_USER -d $POSTGRES_DB -Fc -f /tmp/db.dump'
   ```
   ```bash
   docker cp assistant_postgres:/tmp/db.dump ./db.dump
   ```
   Có sẵn bản dump `storage/backups/before_profile_v7.dump` (trước backfill profile-v7). Nếu dùng bản này thì phải chạy lại backfill như ở bước 5 bên dưới.
2. Trên máy B: `cp .env.example .env` rồi điền giá trị, sau đó:
   ```bash
   docker compose up -d postgres redis
   ```
3. Khôi phục dump:
   ```bash
   docker cp db.dump assistant_postgres:/tmp/
   ```
   ```bash
   docker exec assistant_postgres sh -c 'pg_restore -U $POSTGRES_USER -d $POSTGRES_DB --clean --if-exists /tmp/db.dump'
   ```
4. Khởi động backend và frontend:
   ```bash
   docker compose up -d --build backend frontend
   ```
   Image đã có pymupdf và các thư viện OpenCV.
5. Nếu dùng dump cũ:
   ```bash
   docker cp scripts/backfill/backfill_profiles.py assistant_backend:/tmp/
   ```
   ```bash
   docker exec -e PYTHONPATH=/app -w /app assistant_backend python /tmp/backfill_profiles.py --no-llm
   ```

---

## 3. Bước A: xuất dữ liệu và chạy baseline

### 3.1 Xuất corpus (chỉ đọc DB)
```bash
docker cp scripts/ingestion/export_corpus_md.py assistant_backend:/tmp/
```
```bash
docker exec -e PYTHONPATH=/app -w /app assistant_backend python /tmp/export_corpus_md.py --out /tmp/corpus_md
```
```bash
docker cp assistant_backend:/tmp/corpus_md ./spikes/graphrag/corpus_md
```

Kết quả:
- Mỗi văn bản thành một file `.md`. Phần đầu file là YAML gồm `doc_number`, `doc_date`, `doc_type`, `title`, `issuer`, `signer`, `document_id`; phần thân là các đoạn cha theo thứ tự, kèm tiêu đề mục và số trang.
- File `manifest.json` liệt kê các văn bản.
- Kỳ vọng **41 văn bản**: 37 hành chính, 1 báo cáo tuần, 2 đồ án, 1 sách kỹ thuật.

### 3.2 Baseline hệ hiện tại
1. Gold set (19 câu, `--split all`):
   ```bash
   uv run python scripts/benchmarks/eval_rag.py --split all --save-baseline
   ```
   Kết quả nằm trong `eval_results/`.
2. Bộ câu hỏi quan hệ R1–R15 (mục 6): gửi từng câu qua API và lưu JSONL theo cùng định dạng ở mục 6.3:
   ```bash
   uv run python spikes/graphrag/ask_current.py --questions spikes/graphrag/questions_relation.json --out spikes/graphrag/results/current_relation.jsonl
   ```
   Script `ask_current.py` cần viết, khoảng 30 dòng:
   - với mỗi câu: `POST /conversations` để tạo hội thoại mới, rồi `POST /query {query, conversation_id}`;
   - lưu `message`, `data.citations` (tiêu đề và số hiệu), `status`, độ trễ.
   - Mỗi câu một hội thoại mới để tránh ngữ cảnh lượt trước ảnh hưởng.

---

## 4. Bước B: dựng GraphRAG trên máy B

### 4.1 Neo4j
`spikes/graphrag/docker-compose.neo4j.yml`:
```yaml
services:
  neo4j:
    image: neo4j:5.26-community
    container_name: spike_neo4j
    ports: ["7474:7474", "7687:7687"]
    environment:
      NEO4J_AUTH: neo4j/<mật-khẩu-spike>
      NEO4J_server_memory_heap_initial__size: 512m
      NEO4J_server_memory_heap_max__size: 1g
      NEO4J_server_memory_pagecache_size: 512m
      NEO4J_PLUGINS: '["apoc"]'
    volumes: ["neo4j_data:/data"]
volumes:
  neo4j_data:
```
```bash
docker compose -f spikes/graphrag/docker-compose.neo4j.yml up -d
```
Mở `http://<máy B>:7474` để vào Neo4j Browser.

### 4.2 Môi trường Python riêng cho spike
```bash
cd spikes/graphrag && uv venv --python 3.11 && source .venv/bin/activate
```
```bash
uv pip install "agentic-graphrag[neo4j,extract,agents]" --torch-backend=auto
```

File `spikes/graphrag/.env` (không commit):
```
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<mật-khẩu-spike>
AGENT_LLM_BASE_URL=http://<host 9router>:20128/v1
AGENT_LLM_API_KEY=<key>
AGENT_LLM_MODEL_ID=ag/gemini-3.8-flash-low
```

### 4.3 Kiểm tra trước khi ingest (30 phút, quan trọng)
API thật của thư viện phải đối chiếu với tài liệu của họ: https://ontogr.github.io/agentic-graphrag/ và thư mục `examples/` trong repo. Cần xác nhận:
1. **Embedder:**
   - `build_embedder(...)` có nhận model sentence-transformers cục bộ không;
   - nếu nhận, dùng `AITeamVN/Vietnamese_Embedding` (1024 chiều), giống hệ hiện tại;
   - nếu không, ghi lại model đã dùng. Embedder mặc định là `granite-embedding-small-english-r2`, chỉ cho tiếng Anh.
2. **GLiNER:**
   - checkpoint có cấu hình được không; dùng bản đa ngôn ngữ, ví dụ `urchade/gliner_multi-v2.1`;
   - chạy thử trên 1 đoạn của QĐ 1119 xem có nhận ra tên đơn vị và tên người tiếng Việt không.
3. **Schema/ontology tự định nghĩa:** khai báo bằng code hay file cấu hình.
4. **Giới hạn LLM khi trích:** tên tham số giới hạn số lần gọi LLM cho mỗi lô.

Nếu GLiNER tiếng Việt quá kém (bỏ sót trên 50% thực thể trong 10 mẫu thử), chuyển sang **chế độ trích bằng LLM** có giới hạn ngân sách. Ghi lại lựa chọn này vào báo cáo.

### 4.4 Ontology cho văn bản hành chính
| Loại thực thể | Ví dụ |
|---|---|
| `Document` | QĐ 1119/QĐ-TNVN (số hiệu, ngày, loại) |
| `LegalBasis` (văn bản được viện dẫn) | Nghị định 152/2025/NĐ-CP, Luật Thi đua khen thưởng 2022 |
| `Person` | Đỗ Tiến Sỹ, Trương Thị Hà |
| `OrgUnit` | Ban Thư ký biên tập, Cơ quan thường trú khu vực Tây Nguyên |
| `Award` | Bằng khen của Tổng Giám đốc, Chiến sĩ thi đua cơ sở |
| `Event` | Liên hoan Phát thanh toàn quốc lần thứ XVII |

| Quan hệ | Nghĩa |
|---|---|
| `BASED_ON` (Document → LegalBasis/Document) | dòng "Căn cứ …" |
| `PROPOSED_BY` (Document → Document) | "Theo đề nghị … tại Tờ trình số …" |
| `REPLACES` / `AMENDS` (Document → Document) | "thay thế", "sửa đổi, bổ sung" |
| `SIGNED_BY` (Document → Person) | khối chữ ký |
| `AWARDS` (Document → OrgUnit/Person, thuộc tính `award`) | "Tặng Bằng khen … cho …" |
| `APPOINTS` / `TRANSFERS` / `TERMINATES` (Document → Person) | bổ nhiệm, điều động, chấm dứt hợp đồng |
| `FUNDED_BY` (Document → Document) | "Kinh phí … theo Quyết định số …" |
| `MEMBER_OF` (Person → OrgUnit) | chức vụ, đơn vị |

Thêm mô tả tiếng Việt và 1–2 ví dụ cho từng loại để model trích tốt hơn.

### 4.5 Ingest
Script `spikes/graphrag/ingest.py`, khung dưới đây cần đối chiếu API thật:
```python
store = build_graph_store("neo4j")
embedder = build_embedder("<đường dẫn hoặc id Vietnamese_Embedding>")
graph = Graph.open(schema=ADMIN_SCHEMA, store=store, embedder=embedder,
                   extraction=ExtractionSettings(mode="gliner", ...))
for md in sorted(Path("corpus_md").glob("*.md")):
    graph.add(md)  # hoặc đọc text + metadata từ front matter
```
Cần ghi lại:
- tổng thời gian ingest;
- số lần gọi LLM và số token, nếu thư viện có báo;
- RAM đỉnh, đo bằng `docker stats` và `/usr/bin/time -v`;
- số node và số quan hệ theo từng loại.

Kiểm tra nhanh trong Neo4j Browser:
```cypher
MATCH (n) RETURN labels(n) AS type, count(*) ORDER BY count(*) DESC;
MATCH ()-[r]->() RETURN type(r), count(*) ORDER BY count(*) DESC;
MATCH (d)-[r:AWARDS]->(x) WHERE d.doc_number STARTS WITH '1119' RETURN d, r, x;
```
QĐ 1119 phải có đủ 9 đơn vị được khen.

---

## 5. Chấm chất lượng trích thực thể và quan hệ (Q3)

Lấy ngẫu nhiên **30 quan hệ**, ưu tiên `AWARDS`, `SIGNED_BY`, `BASED_ON`, `REPLACES`. Với mỗi quan hệ, mở đoạn nguồn và chấm:
- **Đúng**: đúng hai đầu, đúng loại quan hệ, có đoạn nguồn chứng minh.
- **Sai đầu**: sai thực thể, sai tên hoặc bị cắt cụt.
- **Sai loại**: hai đầu đúng nhưng loại quan hệ sai.
- **Không căn cứ**: không có đoạn nguồn chứng minh.

Thêm **độ phủ** trên 5 văn bản biết rõ là 1119, 90, 91, 93 và 445: tỷ lệ quan hệ thực tế (đếm tay) được trích ra.
Kết quả đi vào bảng mục 7.

Cũng chấm tình trạng gộp thực thể: "Đỗ Tiến Sỹ" xuất hiện ở các văn bản khác nhau có được gộp làm một không; "Ban Tổ chức cán bộ và Hợp tác quốc tế" và "Ban TCCB&HTQT" có bị tách thành hai không.

---

## 6. Bộ câu hỏi quan hệ R1–R15

Đáp án dưới đây lấy từ dữ liệu đã kiểm trong DB hiện tại. Đáp án ghi *(xác minh)* thì phải kiểm lại bằng SQL ở mục 6.2 trước khi chấm.

| ID | Câu hỏi | Đáp án kỳ vọng | Loại |
|---|---|---|---|
| R1 | Quyết định 1119/QĐ-TNVN tặng Bằng khen cho những tập thể nào? | Đủ 9 đơn vị: Sở VHTTDL Quảng Ninh; Báo và PT-TH Quảng Ninh; TT Quảng cáo và Dịch vụ Truyền thông; Ban Thư ký biên tập; Ban TCCB&HTQT; TT Sản xuất và Lưu trữ chương trình; Văn phòng; CQTT khu vực Đông Bắc; Ban VH-NT-ÂN (VOV3) | liệt kê theo quan hệ |
| R2 | Những quyết định nào căn cứ Nghị định 152/2025/NĐ-CP? | Ít nhất 90, 91, 93, 445, 1119/QĐ-TNVN *(xác minh đủ danh sách)* | ngược quan hệ |
| R3 | Ai ký các quyết định khen thưởng năm 2026? | Đỗ Tiến Sỹ (90, 93, 1119); Ngô Minh Hiển (445); 91 *(xác minh)* | 2 bước |
| R4 | Kinh phí khen thưởng của QĐ 91 và QĐ 93 thực hiện theo văn bản nào? | QĐ 626/QĐ-TNVN ngày 18/3/2025 về chi thưởng | quan hệ FUNDED_BY |
| R5 | Quyết định 1119 được ban hành theo đề nghị tại tờ trình nào? | Tờ trình 293/TTr-TCCBHTQT ngày 13/04/2026 | PROPOSED_BY |
| R6 | Quyết định 91 được ban hành theo tờ trình nào và của ai? | Tờ trình 29/TTr-TCCBHTQT ngày 15/01/2026 của Trưởng ban Ban TCCB&HTQT | 2 bước |
| R7 | Những cá nhân nào được tặng Bằng khen trong QĐ 90/QĐ-TNVN? | Nguyễn Trần Anh Thu; Nguyễn Thuỷ Linh; Phí Hùng Lĩnh (và tập thể Đoàn TN) | liệt kê |
| R8 | Bà Trương Thị Hà được công nhận danh hiệu gì, theo quyết định nào, ai ký? | Chiến sĩ thi đua cơ sở (năm 2025); QĐ 445/QĐ-TNVN ngày 27/02/2026; Ngô Minh Hiển | 3 bước |
| R9 | Quy chế Thi đua, khen thưởng của Đài được ban hành bởi quyết định nào và văn bản nào trong kho viện dẫn nó? | QĐ 1275/QĐ-TNVN ngày 29/5/2023; được 1119 viện dẫn *(xác minh thêm văn bản khác)* | ngược quan hệ |
| R10 | Cơ quan thường trú khu vực Tây Nguyên được khen vì thành tích gì? | Tuyên truyền về bão số 13 Kalmaegi và khắc phục hậu quả mưa lũ năm 2025 (QĐ 93) | quan hệ + thuộc tính |
| R11 | Quyết định nào chấm dứt hợp đồng làm việc của bà Cao Thị Hoa Hương, hiệu lực từ ngày nào? | 80-QĐ/TNVN ngày 28/4/2026; từ 01/5/2026 | thực thể → văn bản |
| R12 | Ông Lưu Phú được điều động theo quyết định nào? | QĐ 808/QĐ-TNVN *(xác minh nơi đến, ngày)* | thực thể → văn bản |
| R13 | Ông Nguyễn Hoàng Mạnh thôi giữ chức vụ gì, theo quyết định nào? | Phó Trưởng phòng Phòng Quản lý kỹ thuật, Trung tâm Kỹ thuật; QĐ 906/QĐ-TNVN | thực thể → văn bản |
| R14 | Đơn vị nào được khen nhiều lần nhất trong năm 2026? | *(tính từ các quan hệ AWARDS năm 2026; xác minh bằng bảng Excel đã tạo)* | tổng hợp trên graph |
| R15 (âm) | Quyết định nào thay thế Quy chế Thi đua khen thưởng 1275/QĐ-TNVN? | Không có trong kho, phải **từ chối hoặc nói không tìm thấy** | từ chối |

Thêm 3–5 câu **ngoài lĩnh vực hành chính** để kiểm tra graph không làm hỏng câu thường. Ví dụ:
- sách multi-agent: "Hai cách điều phối hệ đa agent trong chương 1 là gì?";
- đồ án: "Đồ án của Đậu Văn Nam dùng những kỹ thuật nào cho dữ liệu bảng?".

### 6.1 File câu hỏi
`spikes/graphrag/questions_relation.json`:
```json
{"items": [{"id": "R1", "query": "...", "expected": "...", "must_include": ["Ban Thư ký biên tập", "..."], "should_abstain": false}]}
```
`must_include` là các cụm bắt buộc phải có, dùng để chấm tự động sơ bộ. R15 có `"should_abstain": true`.

### 6.2 SQL xác minh đáp án (chạy trên DB hiện tại, chỉ đọc)
```sql
-- Văn bản nào nhắc tới một văn bản / người cụ thể
SELECT DISTINCT p.doc_number, p.doc_date, p.title
FROM document_chunks c
JOIN document_profiles p ON p.document_id = c.document_id
JOIN documents d ON d.id = c.document_id AND d.is_active
WHERE c.content_raw ILIKE '%152/2025/NĐ-CP%'   -- đổi cụm cần tìm
ORDER BY p.doc_date;

-- Người ký theo profile
SELECT p.doc_number, e->>'name' AS signer
FROM document_profiles p, jsonb_array_elements(p.entities) e
WHERE e->>'kind' = 'signer' ORDER BY p.doc_date;
```

### 6.3 Định dạng kết quả (dùng chung cho cả hai hệ)
Mỗi dòng JSONL:
```json
{"id": "R1", "system": "current|graph", "answer": "...", "citations": [{"title": "...", "doc_number": "..."}], "status": "answered|insufficient|limit", "latency_s": 12.3, "llm_calls": 3}
```

---

## 7. Chấm điểm và báo cáo

### 7.1 Thang chấm cho mỗi câu (chấm tay, có `must_include` hỗ trợ)
- **2**: đúng và đủ, trích dẫn đúng văn bản.
- **1**: đúng một phần (thiếu mục, hoặc thiếu hay sai trích dẫn).
- **0**: sai hoặc bịa.
- Câu âm (R15): **2** khi từ chối hoặc nói không tìm thấy, **0** khi bịa ra văn bản thay thế.

**Độ chính xác trích dẫn** = số trích dẫn trỏ đúng văn bản có chứa thông tin / tổng số trích dẫn.

### 7.2 Bảng kết quả (`spikes/graphrag/REPORT.md`)
| Nhóm | Hệ hiện tại | GraphRAG | Chênh lệch |
|---|---|---|---|
| Gold set 19 câu (điểm/38) | | | |
| Quan hệ R1–R15 (điểm/30) | | | |
| Câu ngoài hành chính | | | |
| Độ chính xác trích dẫn | | | |
| Từ chối đúng (R15) | | | |
| Độ trễ trung bình / p95 (s) | | | |
| Số lần gọi LLM / câu | | | |
| Quan hệ trích đúng (30 mẫu) | — | | |
| Độ phủ quan hệ (5 văn bản) | — | | |
| RAM đỉnh khi ingest / khi hỏi | | | |
| Thời gian ingest 41 văn bản | | | |

### 7.3 Nội dung REPORT.md
1. Cấu hình đã dùng: embedder, GLiNER hay LLM, model, ontology.
2. Bảng 7.2.
3. 3–5 ví dụ minh hoạ cho mỗi chiều: graph thắng và graph thua.
4. Lỗi trích tiếng Việt điển hình.
5. **Khuyến nghị** theo tiêu chí ở mục 0. Nếu nên đưa graph vào thì phác thảo: những quan hệ nào, trích bằng rule hay LLM, bảng Postgres nào.

---

## 8. Rủi ro và cách xử lý

| Rủi ro | Xử lý |
|---|---|
| GLiNER bỏ sót tên đơn vị hoặc người tiếng Việt | Dùng chế độ trích bằng LLM có giới hạn; hoặc chỉ dùng GLiNER cho `Person` và trích quan hệ bằng LLM |
| Lỗi OCR trong văn bản ("cửa Tổng Giám đốc", "lân thứ") làm gộp thực thể sai | Ghi nhận trong báo cáo; không sửa dữ liệu nguồn trong spike |
| Thư viện không nhận embedder cục bộ | Dùng embedder đa ngôn ngữ khác thư viện hỗ trợ; ghi rõ vì kết quả RAG thường sẽ bị ảnh hưởng |
| Không truy cập được 9router từ máy B | Dùng key API riêng (phương án 2 ở mục 2.1); ghi lại model vì kết quả phụ thuộc model |
| Bị giới hạn tốc độ (429) | Chạy tuần tự, nghỉ giữa các câu; tiếp tục được từ file JSONL |
| Thiếu RAM | Neo4j heap 1g; embedder và GLiNER chạy GPU nếu có; không chạy song song với stack hiện tại nếu máy yếu |

---

## 9. Checklist

- [ ] A1 Xuất corpus `.md` (41 văn bản) và `manifest.json`
- [ ] A2 Baseline gold set (`eval_rag.py --split all --save-baseline`)
- [ ] A3 Viết `questions_relation.json` (R1–R15 + câu ngoài hành chính), xác minh đáp án bằng SQL
- [ ] A4 Baseline R1–R15 trên hệ hiện tại (`ask_current.py`)
- [ ] B1 Máy B: Docker, Python 3.11, uv, truy cập được LLM
- [ ] B2 Neo4j chạy, mở được Browser
- [ ] B3 venv spike, cài `agentic-graphrag`
- [ ] B4 Kiểm tra embedder tiếng Việt, GLiNER đa ngôn ngữ, schema tự định nghĩa (mục 4.3)
- [ ] B5 Ingest 41 văn bản, ghi thời gian, RAM, số node/quan hệ
- [ ] B6 Chạy gold set + R1–R15 trên graph và lưu JSONL
- [ ] C1 Chấm 30 quan hệ và độ phủ 5 văn bản
- [ ] C2 Chấm điểm hai hệ, điền bảng 7.2
- [ ] C3 Viết `REPORT.md` kèm khuyến nghị
