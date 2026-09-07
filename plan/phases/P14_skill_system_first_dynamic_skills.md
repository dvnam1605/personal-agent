> Active phase specification for P14. Read `../MASTER_PLAN.md` first.
> Do not implement any downstream phase (P15+) until the user sends `APPROVED P14`.

# P14 — SKILL SYSTEM + FIRST DYNAMIC SKILLS SPECIFICATION

## 1. Objective & Architectural Scope

Phase 14 establishes the dynamic skill substrate of the assistant: reusable, composable multi-step procedures defined as structured operational documents rather than hard-coded graphs.
1. **Dynamic Procedure Execution**: Provide high-level agents (Specialists and future Supervisor) with structured procedural templates to solve recurring multi-domain goals without rigid hard-coding.
2. **SkillRegistry**: Central discovery, versioning, capability-matching, and validation engine for skills.
3. **First Production Skills**:
   - `MeetingPrepSkill`: Multi-source synthesis across Calendar, Gmail, Drive, and RAG to produce comprehensive meeting briefs.
   - `EmailFollowUpSkill`: Action item extraction, thread synthesis, and contextual draft generation.

### Governing Architectural Rules
- **Procedural Guidance vs. Execution Authority (§18A.2)**: Skills provide structured instructions, schemas, and verification criteria; execution authority remains strictly within first-party runtimes (`SpecialistAgent`, capability gate, and policy engine).
- **Capability Gating on Skills (§14.2)**: A skill cannot grant tools or bypass agent capability gates. If a skill requires `google_workspace.gmail.read` and the calling agent lacks that capability, the skill cannot be activated.
- **Fail-Closed Safety (§4.1)**: Skills declaring mutation steps MUST explicitly declare their required capabilities and cannot bypass user approval or run in read-only mode.
- **No Early Static Graphs (§13)**: P14 focuses strictly on *dynamic* procedural execution. Static compiled `StateGraph` subgraphs belong to P15/P19.

---

## 2. Entry Criteria

Before P14 implementation begins, all of the following MUST be satisfied:
- [ ] **APPROVED P13**: `KnowledgeResearchAgent` operational, with strict read-only boundary and RAG/Drive synthesis verified.
- [ ] **APPROVED P12**: `CommunicationAgent` and `CalendarAgent` operational with tested tool execution.
- [x] **Specialist Runtime Operational (P11)**: Bounded ReAct engine, token budgets, and capability gate active.
- [x] **Spill Storage Operational (P02/P11)**: Spill store available for large intermediate skill artifacts.

---

## 3. Detailed Component Specifications

### 3.1. Skill Definition Contract (`app/domain/models/skill.py`)

```python
class SkillMetadata(BaseModel):
    """Metadata and capability requirements for a registered skill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str = Field(min_length=10, max_length=500)
    required_capabilities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(
        default_factory=list,
        description="Intent trigger phrases or regex keywords for skill matching.",
    )
    is_mutation: bool = Field(
        default=False,
        description="Whether this skill involves write or send operations requiring approval.",
    )


class SkillStep(BaseModel):
    """A single procedural step in a skill workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_index: int = Field(ge=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required_capability: str | None = None
    expected_outputs: list[str] = Field(default_factory=list)
    is_optional: bool = Field(default=False)


class SkillDefinition(BaseModel):
    """Complete specification of a reusable skill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metadata: SkillMetadata
    inputs_schema: dict[str, Any] = Field(default_factory=dict)
    steps: list[SkillStep] = Field(min_length=1)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    safety_constraints: list[str] = Field(default_factory=list)
    completion_criteria: str = Field(min_length=1)
```

### 3.2. SkillRegistry (`app/services/skills/registry.py`)

```python
class SkillRegistry:
    """Manages skill discovery, validation, and intent-based matching."""

    def __init__(self, skills_dir: Path | None = None) -> None: ...
    def register(self, skill: SkillDefinition) -> None: ...
    def get(self, name: str, version: str | None = None) -> SkillDefinition | None: ...
    def match(self, intent_or_query: str, available_capabilities: set[str]) -> list[SkillDefinition]: ...
    def load_from_directory(self, directory: Path) -> int: ...
    def list_all(self) -> list[SkillMetadata]: ...
```

### 3.3. Initial Production Skills (`skills/`)

1. **`skills/meeting-prep/SKILL.md`**:
   - **Step 1**: Find target calendar event (`calendar.search_events`).
   - **Step 2**: Extract participants and agenda topics.
   - **Step 3**: Collect recent communications from participants (`gmail.list_messages`).
   - **Step 4**: Collect relevant documents and internal RAG context (`rag.search`, `drive.search_files`).
   - **Step 5**: Identify unresolved action items and discussion points.
   - **Step 6**: Synthesize structured Meeting Brief artifact.

2. **`skills/email-follow-up/SKILL.md`**:
   - **Step 1**: Retrieve email thread history (`gmail.get_thread`).
   - **Step 2**: Extract sender questions and explicit commitments.
   - **Step 3**: Query relevant internal knowledge for answers.
   - **Step 4**: Formulate draft reply with clear citations and outstanding questions.

---

## 4. Safety & Security Invariants

1. **Gate Intersection**: When an agent executes with a skill, the effective tool view is the **intersection** of the agent's capability view and the skill's required capabilities.
2. **Approval Plane Preserved**: A skill step that mutates external state (`gmail.create_draft`, `calendar.create_event`) MUST supply or request an `approval_token`.
3. **No Dynamic Code Evaluation**: Skills are declarative markdown + YAML/JSON data structures; execution of arbitrary Python code from skill files is strictly prohibited.

---

## 5. Verification & Test Matrix

| Test Case | Target File | Verification Criteria |
| :--- | :--- | :--- |
| Skill Registration & Validation | `tests/unit/services/test_skill_registry.py` | Validates schema, rejects invalid version strings or missing steps. |
| Intent Matching & Gating | `tests/unit/services/test_skill_registry.py` | Matches query to triggers; filters out skills whose required capabilities exceed caller permissions. |
| MeetingPrep Skill Parsing | `tests/unit/services/test_skill_definitions.py` | Verifies `meeting-prep/SKILL.md` parses cleanly into `SkillDefinition` with 6 ordered steps. |
| EmailFollowUp Skill Parsing | `tests/unit/services/test_skill_definitions.py` | Verifies `email-follow-up/SKILL.md` parses cleanly into `SkillDefinition`. |
| Skill Execution Safety | `tests/unit/services/test_skill_execution.py` | Asserts mutation skill steps cannot execute in read-only view or without approval token. |
