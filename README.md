# Personal AI Assistant

A capability-bounded, multi-agent AI assistant designed for productivity and scale.

## Architecture
- 3 Execution paths: Direct Specialist, Known Workflow Graphs, Open-ended Supervisor DAG.
- 4 Core Specialist Agent roles: `SupervisorAgent`, `CommunicationAgent`, `CalendarAgent`, `KnowledgeResearchAgent`.
- Strict capability gating, memory gating, and human-in-the-loop (HITL) policy verification.

## Development Setup
```bash
uv venv
uv pip install -e ".[dev]"
```

## Running Tests
```bash
pytest
```
