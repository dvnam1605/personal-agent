# TẬP 3: SPECIALIST AGENTS & BOUNDED REACT RUNTIME (SPECIALIST RUNTIME)

> **Cấp độ tài liệu**: Sách tham khảo kỹ thuật chuyên sâu - Tập 3/6 (Technical Architecture Manual - Volume 3).
> **Thành phần liên quan**: [`app/agents/specialist/react.py`](file:///d:/Code/personal_ai_assistant/app/agents/specialist/react.py), [`guard.py`](file:///d:/Code/personal_ai_assistant/app/agents/specialist/guard.py), [`declarations.py`](file:///d:/Code/personal_ai_assistant/app/agents/declarations.py).

---

## 1. RANH GIỚI TRÁCH NHIỆM CỦA 4 SPECIALIST AGENTS (AGENT BOUNDARIES)

Hệ thống tuân thủ nghiêm ngặt nguyên tắc phân định năng lực (Least Privilege). Mỗi Agent chỉ được sở hữu các công cụ thuộc miền tác vụ của mình:

```mermaid
classDiagram
    class SupervisorAgent {
        +Domain: GENERAL / MULTI-DOMAIN
        +Tách nhỏ yêu cầu phức tạp thành DAG JSON
        +Phân quyền nhiệm vụ cho Specialists
        +Chỉ replan khi có task lỗi (Tối đa 2 lần)
        -KHÔNG chạy cho task đơn miền
        -KHÔNG trực tiếp gọi API của Google
    }

    class CommunicationAgent {
        +Domain: COMMUNICATION
        +gmail.search_messages()
        +gmail.get_thread()
        +gmail.create_draft()
        +gmail.send_draft()* [Cần Approval]
        +contacts.search_contacts()
        -KHÔNG sờ vào Calendar hoặc RAG tài liệu
    }

    class CalendarAgent {
        +Domain: CALENDAR
        +calendar.list_events()
        +calendar.find_free_slots() [Số học lịch biểu]
        +calendar.create_event()* [Cần Approval]
        +calendar.update_event()* [Cần Approval]
        -KHÔNG tự tính toán giờ giấc bằng LLM
        -KHÔNG đọc email hoặc tài liệu
    }

    class KnowledgeResearchAgent {
        +Domain: KNOWLEDGE_RESEARCH
        +retrieval.retrieve()
        +retrieval.synthesize()
        +drive.search_files()
        +drive.download_file()
        +web.search()
        +Chế độ: INTERNAL / WEB / MIXED
        -LUÔN LUÔN READ-ONLY 100%
        -Bảo đảm trích dẫn [evidence_id]
        -Miễn nhiễm prompt injection
    }

    SupervisorAgent ..> CommunicationAgent : Điều phối task
    SupervisorAgent ..> CalendarAgent : Điều phối task
    SupervisorAgent ..> KnowledgeResearchAgent : Điều phối task
```

---

## 2. SEQUENCE DIAGRAM: BOUNDED REACT SPECIALIST RUNNER (`react.py`)

Tất cả các Specialist Agent đều được điều phối vận hành bởi `SpecialistRunner`:

```mermaid
sequenceDiagram
    autonumber
    participant App as Orchestrator / Caller
    participant Runner as SpecialistRunner
    participant Mode as ModeSelector
    participant Gate as CapabilityGate
    participant LLM as ChatBackend (LLM)
    participant Guard as RepeatToolGuard
    participant Tools as ToolExecutor (Gated View)

    App->>Runner: run(task, agent, tools, budget)
    Runner->>Mode: select(task, agent) -> DIRECT hoặc BOUNDED_REACT
    Runner->>Gate: build_view(agent_name, read_only=task.permit_mutations)
    Note over Gate,Runner: Lọc sạch các công cụ cấm hoặc mutation nếu là read-only

    alt Chế độ DIRECT
        Runner->>LLM: 1-Shot Completion (system preamble + goal + tools)
        alt Không có tool call
            Runner-->>App: SpecialistOutcome (SUCCESS, câu trả lời trực tiếp)
        else Có tool call nhưng cho phép leo thang
            Note over Runner: Chuyển sang BOUNDED_REACT
        end
    end

    loop Bounded ReAct Iterations (Max steps: 4-6, Max tools: 8-10)
        Runner->>Runner: Kiểm tra Pre-step Budget (timeout, tokens, step count)
        Runner->>LLM: Gửi transcripts + tool schemas + report tool
        LLM-->>Runner: AssistantTurn (text + tool_calls)
        Runner->>Runner: Kiểm tra Post-LLM Budget

        alt Không còn tool call nào
            Runner-->>App: SpecialistOutcome (SUCCESS)
        end

        loop Cho từng ToolCall
            alt Gọi specialist.report (Báo cáo hoàn thành)
                Runner->>Runner: Parse & Validate SpecialistReport
                Runner-->>App: Trả về kết quả (SUCCESS / BLOCKED / NEEDS_APPROVAL)
            else Gọi Business Tool (vd: gmail.search, calendar.find_free_slots)
                Runner->>Guard: Kiểm tra trùng lặp (tool, args)
                alt Trùng lặp >= 2 lần
                    Runner->>LLM: Chèn System Reminder cảnh báo loop
                else Trùng lặp >= 3 lần (Circuit Breaker)
                    Runner-->>App: Dừng runner với lý do NO_PROGRESS
                end
                Runner->>Tools: execute(tool_name, arguments, context)
                Tools-->>Runner: ToolResult (output, success, latency)
            end
        end
    end
```

---

## 3. THUẬT TOÁN Anti-Loop CIRCUIT BREAKER (`guard.py`)

### 3.1. Đặt Vấn Đề
Trong luồng ReAct (Reason + Act), các mô hình LLM có nguy cơ rơi vào trạng thái kẹt lặp (Infinite Tool Loop): LLM gọi một công cụ, nhận về kết quả lỗi hoặc rỗng, nhưng ở bước tiếp theo lại tiếp tục gọi lại chính công cụ đó với bộ tham số y hệt. Việc này gây lãng phí token và làm đơ hệ thống.

### 3.2. Thuật Toán `RepeatToolGuard` Chi Tiết

1. **Tính toán Hash nguyên tử**:
   Mỗi khi LLM sinh một `ToolCall(name, args)`, `RepeatToolGuard` chuẩn hóa dictionary `args` (sắp xếp key) và tính toán chuỗi băm MD5:
   $$\text{ToolHash} = \text{MD5}(\text{tool\_name} + \text{json\_dumps}(\text{sorted\_args}))$$

2. **Theo dõi tần suất (Frequency Tracking)**:
   Hệ thống lưu lịch sử các `ToolHash` trong phạm vi lượt chạy của Agent.

3. **Xử lý các ngưỡng trùng lặp**:
   * **Ngưỡng trùng = 1**: Cho phép thực thi bình thường.
   * **Ngưỡng trùng = 2 (Reminder Warning)**:
     Thực thi tool, nhưng ở lượt gọi LLM tiếp theo, Runner chủ động chèn thêm một thông điệp System Reminder vào transcript:
     > *"Cảnh báo hệ thống: Bạn đã gọi công cụ '{tool_name}' với cùng bộ tham số 2 lần liên tiếp mà không có tiến triển mới. Bạn PHẢI thay đổi giá trị tham số hoặc sử dụng một công cụ khác."*
   * **Ngưỡng trùng = 3 (Circuit Breaker)**:
     Kích hoạt ngắt mạch khẩn cấp! Runner dừng vòng lặp ReAct ngay lập tức và trả về `SpecialistOutcome(status=NO_PROGRESS, reasoning="Circuit breaker triggered: Tool called with identical arguments 3 times.")`.

---

## 4. QUẢN LÝ NGÂN SÁCH THỰC THI (BUDGET & RUNTIME LIMITS)

Mọi lượt chạy của `SpecialistRunner` đều được kiểm soát bởi đối tượng `ExecutionBudget`:

| Tham Số Ngân Sách | Giá Trị Mặc Định | Hành Động Khi Vượt Ngưỡng |
| :--- | :--- | :--- |
| `max_steps` | 4–6 bước | Dừng ReAct loop, chuyển sang bước tổng hợp cuối với thông báo hoàn thành một phần. |
| `max_tool_calls` | 8–10 lượt | Cấm LLM phát sinh thêm tool call mới; bắt buộc dùng dữ liệu đã có để trả lời. |
| `timeout_seconds` | 30 giây | Ném ngoại lệ `TimeoutError`, hủy bỏ các task async đang chờ. |
| `max_tokens` | 4,000 tokens | Ngắt lượt gọi LLM và cảnh báo tràn bộ nhớ ngữ cảnh. |

---

*Xem tiếp chi tiết An toàn & Phê duyệt tại Tập 4: [`04-security-policy-engine-and-hitl.md`](file:///d:/Code/personal_ai_assistant/docs/architecture/04-security-policy-engine-and-hitl.md)*
