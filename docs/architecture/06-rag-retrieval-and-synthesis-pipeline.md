# TẬP 6: PIPELINE TÌM KIẾM & TỔNG HỢP CÂU TRẢ LỜI (RAG RETRIEVAL & SYNTHESIS - P10)

> **Cấp độ tài liệu**: Sách tham khảo kỹ thuật chuyên sâu - Tập 6/6 (Technical Architecture Manual - Volume 6).
> **Thành phần liên quan**: [`app/services/retrieval/pipeline.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/pipeline.py), [`dense.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/dense.py), [`sparse.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/sparse.py), [`hybrid.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/hybrid.py), [`rerank.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/rerank.py), [`expansion.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/expansion.py), [`packing.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/packing.py), [`sufficiency.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/sufficiency.py), [`retry.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/retry.py), [`injection_boundary.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/injection_boundary.py), [`synthesis.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/synthesis.py).

---

## 1. SƠ ĐỒ LUỒNG PIPELINE RETRIEVAL & SYNTHESIS (P10 END-TO-END)

```mermaid
flowchart TD
    Q[User Search Query] --> ScopeFilter[1. Security Scope Filtering]

    ScopeFilter --> Dense[2. Dense Search: pgvector Cosine]
    ScopeFilter --> Sparse[2. Sparse Search: PostgreSQL FTS]

    Dense & Sparse --> RRF[3. Reciprocal Rank Fusion RRF]
    RRF --> Diversity[3. Apply Per-Document Diversity Cap]
    Diversity --> Rerank[3. ViRanker Cross-Encoder Score Filter]

    Rerank --> Exp{4. Expansion Policy}
    Exp -->|NONE| Fused[CHILD Chunks Only]
    Exp -->|NEIGHBORS| Sibling[Include Sibling Chunks]
    Exp -->|PARENT| Parent[Retrieve Full Level 0 Parent]

    Fused & Sibling & Parent --> Pack[4. Context Token Packing]
    Pack --> SuffCheck{5. Deterministic Sufficiency Check}

    SuffCheck -->|Insufficient| RetryCheck{Retry Attempts under 2?}
    RetryCheck -->|Yes| QueryReform[Query Reformulation Retry]
    QueryReform --> Dense
    RetryCheck -->|No| NoAnswer[Return Explicit Vietnamese Fallback]

    SuffCheck -->|Sufficient| InjectionGate[6. Prompt Injection Boundary Tag]
    InjectionGate --> Synth[6. Answer Synthesizer LLM Generation]
    Synth --> Result[SynthesisResult with Evidence Citations]
```

---

## 2. CHI TIẾT THỰC THI KỸ THUẬT 6 BƯỚC RETRIEVAL

### 2.1. Bước 1: Security Scope Filtering (`provider.py`)

* **Nhiệm vụ**: Phân quyền dữ liệu, ngăn chặn User A đọc được tài liệu riêng tư của User B.
* **Cơ chế**: Mọi truy vấn SQL trong luồng Retrieval đều bị áp đặt điều kiện:
  ```sql
  WHERE d.is_active = TRUE 
    AND (d.user_id = :requester_id OR d.user_id IS NULL)
  ```

---

### 2.2. Bước 2: Concurrent Hybrid Search (`hybrid.py`)

Thực thi đồng thời luồng tìm kiếm ngữ nghĩa và luồng tìm kiếm từ khóa thông qua `asyncio.gather`:

1. **Dense Retrieval Leg ([`dense.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/dense.py))**:
   * Nhúng `query.search_query` thành vector 1024d bằng `AITeamVN/Vietnamese_Embedding`.
   * Thực thi SQL tìm kiếm vector theo khoảng cách Cosine Distance (`<=>`):
     ```sql
     SELECT c.id AS chunk_id, c.parent_id, c.document_id, c.content_raw,
            (c.embedding <=> ':vector'::vector) AS score
     FROM document_chunks c JOIN documents d ON c.document_id = d.id
     WHERE c.hierarchy_level = 1 AND d.is_active = TRUE AND ...
     ORDER BY c.embedding <=> ':vector'::vector ASC LIMIT :top_k_dense;
     ```

2. **Sparse Retrieval Leg ([`sparse.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/sparse.py))**:
   * Đưa chuỗi truy vấn qua `websearch_to_tsquery` của PostgreSQL để biến câu nói tự nhiên thành tsquery an toàn (loại bỏ nguy cơ lỗi cú pháp và SQL Injection).
   * Thực thi SQL tìm kiếm từ khóa Full-Text Search:
     ```sql
     SELECT c.id AS chunk_id, c.parent_id, c.document_id, c.content_raw,
            ts_rank_cd(c.search_vector, websearch_to_tsquery('public.vietnamese_simple', :query)) AS score
     FROM document_chunks c JOIN documents d ON c.document_id = d.id
     WHERE c.hierarchy_level = 1 AND c.search_vector @@ websearch_to_tsquery('public.vietnamese_simple', :query)
     ORDER BY score DESC LIMIT :top_k_sparse;
     ```

---

### 2.3. Bước 3: Fusion, Diversity Cap & Cross-Encoder Reranking

1. **Reciprocal Rank Fusion (RRF)**:
   Kết hợp kết quả từ Dense và Sparse Leg theo công thức thứ hạng:
   $$\text{fusion\_score}(c) = \sum_{s \in \{\text{dense}, \text{sparse}\}} \frac{1}{k + \text{rank}_s(c)}, \quad k = 60$$

2. **Diversity Cap ([`diversity.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/diversity.py))**:
   Áp dụng `per_document_cap` giới hạn số chunk tối đa cho từng file (vd: tối đa 3-4 chunks/tài liệu).

3. **ViRanker Cross-Encoder Reranking ([`rerank.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/rerank.py))**:
   * Đưa các candidate đã fuse qua mô hình local `namdp-ptit/ViRanker`.
   * Đánh giá mối tương quan trực tiếp trên thang điểm Sigmoid $[0.0, 1.0]$.
   * **Score Threshold Filter**: Lọc bỏ tất cả các candidate có $\text{rerank\_score} < 0.3$.

---

### 2.4. Bước 4: Context Expansion & Token Packing

1. **Expansion Policy ([`expansion.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/expansion.py))**:
   * `NONE`: Chỉ lấy văn bản của CHILD chunk.
   * `NEIGHBORS`: Lấy thêm các CHILD chunk anh em lân cận cùng `parent_id`.
   * `PARENT`: SQL query kéo trọn vẹn văn bản thô của PARENT chunk (Level 0 - 1600 tokens).

2. **Greedy Token Packing ([`packing.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/packing.py))**:
   Lần lượt nhồi các candidate vào `EvidenceBundle` cho đến khi vừa khít `context_token_budget` (vd: 3000 tokens), giữ nguyên vẹn cấu trúc câu.

---

### 2.5. Bước 5: Deterministic Sufficiency Check & Bounded Retry

```mermaid
flowchart TD
    Start[EvidenceBundle] --> CheckItems{Số lượng item == 0?}
    CheckItems -->|Đúng| Insufficient[INSUFFICIENT]
    CheckItems -->|Sai| CheckScore{Tất cả score < min_threshold?}
    CheckScore -->|Đúng| Insufficient
    CheckScore -->|Sai| CheckCompare{Chế độ so sánh tài liệu đủ file?}
    CheckCompare -->|Thiếu file| Partial[PARTIAL]
    CheckCompare -->|Đủ file| Sufficient[SUFFICIENT]

    Insufficient & Partial --> RetryGate{Lần thử < 2?}
    RetryGate -->|Còn lượt| RetryExec[Query Reformulation -> Tăng Top-K, nới filter]
    RetryGate -->|Hết 2 lượt| NoAnswer[Từ chối trả về: 'Tôi không tìm thấy đủ tài liệu nội bộ...']
    Sufficient --> Pass[Cho phép tổng hợp câu trả lời]
```

* **Deterministic SufficiencyChecker ([`sufficiency.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/sufficiency.py))**:
  Đánh giá độ đủ dữ liệu qua quy tắc deterministic **(0% tốn LLM judge)**:
  * Không có item nào $\rightarrow$ `INSUFFICIENT`.
  * Tất cả các item đều có `rerank_score < 0.3` $\rightarrow$ `INSUFFICIENT`.
* **Bounded Retry Strategy ([`retry.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/retry.py))**:
  * Nếu `INSUFFICIENT` hoặc `PARTIAL`, tự động thực hiện Query Reformulation (tăng K từ 20 lên 40, chuyển sang policy `PARENT`) và retry **tối đa 2 lần**.
  * Nếu sau 2 lượt thử vẫn không đủ dữ liệu $\rightarrow$ Trả về câu từ chối chuẩn tiếng Việt, triệt tiêu ảo giác (Hallucination).

---

### 2.6. Bước 6: Prompt Injection Defense & Answer Synthesis

1. **Injection Boundary ([`injection_boundary.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/injection_boundary.py))**:
   Bọc toàn bộ văn bản trích xuất trong thẻ XML cấu trúc:
   ```xml
   <retrieved_document id="doc-123" evidence_id="ev-456">
   [Nội dung văn bản từ Database]
   </retrieved_document>
   ```
   System Prompt chỉ thị nghiêm ngặt: Dữ liệu trong thẻ này là **Untrusted Input**, cấm không được ghi đè chỉ dẫn hệ thống.

2. **PromptAnswerSynthesizer ([`synthesis.py`](file:///d:/Code/personal_ai_assistant/app/services/retrieval/synthesis.py))**:
   Bộ tổng hợp câu trả lời dựa trên LLM và ngữ cảnh tài liệu xác thực, tuân thủ nghiêm ngặt các nguyên tắc thiết kế:
   * **Clean PEP 8 Architecture & Top-Level Imports**: Toàn bộ các thư viện hỗ trợ (`json`, `httpx`, `get_settings`, prompts) được nạp ở mức top-level chuẩn mực, loại bỏ các inline/delayed import trong hàm, bảo đảm hiệu năng nạp mô-đun và vượt qua 100% kiểm tra linter `ruff` & `pyright`.
   * **Cơ Chế Footnote Citation Mapping**:
     * Prompt chỉ thị LLM đính kèm mã trích dẫn thô ứng với từng bằng chứng theo cú pháp `[evidence_id]` (hoặc `[<uuid>]`).
     * `PromptAnswerSynthesizer` sử dụng Regex `_CITE_RE` dò quét toàn bộ các thẻ trích dẫn thô trong văn bản sinh ra, đối soát với `EvidenceBundle`, và chuyển đổi thành dạng số chú thích chân trang trực quan `[1]`, `[2]`, `[3]`.
     * Cấu trúc kết quả `SynthesisResult` bao gồm:
       * `answer`: Văn bản câu trả lời hoàn chỉnh đã thay thế footnote số học.
       * `citations`: Mảng các đối tượng `Citation` theo đúng thứ tự footnote, cung cấp `document_id`, tiêu đề tài liệu (`title`), trang tài liệu (`page_number`), số điểm liên quan (`score`) và đoạn trích dẫn ngữ cảnh (`snippet`).
       * `groundedness_score`: Điểm số đánh giá mức độ bám sát bằng chứng.
   * **Dual-Mode Synthesis API (Batch & Realtime Streaming)**:
     * `synthesize(...)`: Trả về `SynthesisResult` hoàn chỉnh sau khi LLM kết thúc thế hệ câu trả lời.
     * `synthesize_stream(...)`: Async Generator stream trực tiếp các mảnh văn bản (token deltas) tới client qua giao thức SSE (Server-Sent Events), giúp giảm thiểu tối đa Time-To-First-Token (TTFT). Khi stream kết thúc, generator phát sự kiện trích dẫn cuối cùng với danh sách metadata nguồn.

---

## 3. BẢNG TỔNG HỢP HẰNG SỐ KỸ THUẬT & MÔ HÌNH DỮ LIỆU CƠ SỞ (CORE ERD)

### 3.1. Bảng Thông Số Kỹ Thuật Trọng Yếu

| Hạng mục | Tham số / Model | Giá trị thực tế trong mã nguồn | Ý nghĩa kiến trúc |
| :--- | :--- | :--- | :--- |
| **Embedding Model** | Kích thước & Tên model | `AITeamVN/Vietnamese_Embedding` (1024 dims) | Tối ưu riêng cho văn bản tiếng Việt; L2-normalized. |
| **Reranker Model** | Tên model & Ngưỡng | `namdp-ptit/ViRanker` (Score $\ge 0.3$) | Cross-Encoder lọc bỏ hiệu quả các chunk kém tương quan. |
| **Chunking Size** | Parent / Child Target | Parent: 1600 tokens · Child: 500 tokens | Bảo toàn ngữ cảnh câu/đoạn, không cắt vỡ bảng. |
| **Hybrid Fusion** | Thuật toán & Hằng số | RRF ($k=60$) | Cân bằng hoàn hảo giữa Dense (Ý nghĩa) và Sparse (Từ khóa). |
| **Retry Cap** | Retrieval Retry Limit | Maximum **2** attempts | Ngăn lặp vô tận, đảm bảo Latency p95 $\le 800$ms. |
| **ReAct Loop** | Limits | Max 4–6 steps, max 8-10 tool calls | Bảo vệ ngắt mạch (Circuit Breaker) nếu Agent kẹt lặp tool call. |

---

### 3.2. Sơ Đồ Mô Hình Dữ Liệu Cơ Sở (Core 14 Database Tables ERD)

```mermaid
erDiagram
    users ||--o{ conversations : owns
    users ||--o{ assistant_runs : initiates
    users ||--o{ documents : owns
    users ||--o{ memories : recalls

    conversations ||--o{ messages : contains
    conversations ||--o{ assistant_runs : scopes

    assistant_runs ||--o{ tool_executions : logs
    assistant_runs ||--o{ llm_executions : tracks
    assistant_runs ||--o{ approval_requests : requires

    documents ||--o{ document_chunks : partitions
    document_chunks ||--o{ document_chunks : "parent/child hierarchy"

    documents {
        string id PK
        string logical_document_id
        int version_number
        string fingerprint
        boolean is_active
        json metadata
    }

    document_chunks {
        string id PK
        string document_id FK
        string parent_id FK
        int hierarchy_level "0=PARENT, 1=CHILD"
        string node_type
        text content_raw
        vector_1024 embedding
        tsvector search_vector
        json heading_path
    }

    assistant_runs {
        string id PK
        string user_id FK
        string session_id FK
        string route_type
        string status
        int react_steps
        int tool_call_count
        float total_latency_ms
    }

    approval_requests {
        string id PK
        string run_id FK
        string action_type
        string tool_name
        string risk_level
        string status "pending/approved/rejected"
        json parameters
    }
```
