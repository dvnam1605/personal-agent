"""Spill storage and preview retention policy subsystem for oversized tool outputs."""

import hashlib
import json
import re
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path

import structlog

from app.domain.errors import NotFoundError, ValidationError
from app.domain.models.spill import SpilledOutput, SpillPolicyConfig, SpillRef
from app.domain.models.tool import ToolResult

logger = structlog.get_logger(__name__)


def _sanitize_name(suggested_name: str | None, default: str = "output") -> str:
    """Sanitize suggested name to a single safe filename segment."""
    if not suggested_name:
        return default
    # Strip any path separators, parent traversal, or invalid chars
    cleaned = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", suggested_name.strip())
    cleaned = re.sub(r"\.+", ".", cleaned)
    cleaned = cleaned.strip("._")
    return cleaned or default


def _utf8_truncate_head(text: str, max_bytes: int) -> str:
    """Truncate text from head so its UTF-8 encoding <= max_bytes without splitting multi-byte characters."""
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # Truncate raw bytes and decode ignoring partial multibyte char at the boundary
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _utf8_truncate_tail(text: str, max_bytes: int) -> str:
    """Truncate text from tail so its UTF-8 encoding <= max_bytes without splitting multi-byte characters."""
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # Take slice from the end and decode ignoring partial multibyte char at start of boundary
    return encoded[-max_bytes:].decode("utf-8", errors="ignore")


class SpillStore(ABC):
    """Abstract interface for session-scoped spill storage backends."""

    @abstractmethod
    def save_text(
        self,
        session_id: str,
        content: str,
        *,
        tool_name: str | None = None,
        call_id: str | None = None,
        suggested_name: str | None = None,
    ) -> SpillRef:
        """Persist full verbatim text and return a SpillRef."""
        ...

    @abstractmethod
    def read_text(self, locator: str, offset: int = 0, limit: int | None = None) -> str:
        """Read a slice of the spilled text starting from character offset with optional limit."""
        ...

    @abstractmethod
    def get_ref(self, locator: str) -> SpillRef | None:
        """Retrieve the SpillRef metadata for a locator if present."""
        ...

    @abstractmethod
    def list_spills(self, session_id: str) -> list[SpillRef]:
        """List all spill artifacts belonging to a session."""
        ...


class InMemorySpillStore(SpillStore):
    """In-memory implementation of SpillStore for fast ephemeral storage and unit testing."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[SpillRef, str]] = {}

    def save_text(
        self,
        session_id: str,
        content: str,
        *,
        tool_name: str | None = None,
        call_id: str | None = None,
        suggested_name: str | None = None,
    ) -> SpillRef:
        if not session_id or not session_id.strip():
            raise ValidationError("session_id must be provided to save spill text.")

        artifact_id = f"{uuid.uuid4().hex[:12]}_{_sanitize_name(suggested_name or tool_name)}"
        locator = f"spill://{session_id.strip()}/{artifact_id}"
        encoded = content.encode("utf-8")
        byte_count = len(encoded)
        char_count = len(content)

        retrieval_hint = (
            f"Use tool 'spill.slice' or 'spill.fetch' with session_id='{session_id.strip()}' "
            f"and locator='{locator}' plus optional offset/limit to inspect sections."
        )

        ref = SpillRef(
            locator=locator,
            byte_count=byte_count,
            character_count=char_count,
            retrieval_hint=retrieval_hint,
            session_id=session_id.strip(),
            artifact_id=artifact_id,
            tool_name=tool_name,
            call_id=call_id,
            created_at=datetime.now(UTC),
        )

        self._entries[locator] = (ref, content)
        return ref

    def read_text(self, locator: str, offset: int = 0, limit: int | None = None) -> str:
        if locator not in self._entries:
            raise NotFoundError(
                f"Spill artifact '{locator}' was not found.",
                details={"locator": locator},
            )
        _, content = self._entries[locator]
        if offset < 0:
            offset = 0
        if limit is None:
            return content[offset:]
        return content[offset : offset + limit]

    def get_ref(self, locator: str) -> SpillRef | None:
        entry = self._entries.get(locator)
        return entry[0] if entry else None

    def list_spills(self, session_id: str) -> list[SpillRef]:
        target_session = session_id.strip()
        return [ref for ref, _ in self._entries.values() if ref.session_id == target_session]


class LocalFileSpillStore(SpillStore):
    """Local filesystem spill store isolating artifacts by hashed session directory."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._meta_cache: dict[str, SpillRef] = {}

    def _session_dir(self, session_id: str) -> Path:
        session_hash = hashlib.sha256(session_id.strip().encode("utf-8")).hexdigest()[:16]
        dir_path = self.base_dir / f"session-{session_hash}"
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path

    def _resolve_locator_path(self, locator: str) -> Path:
        """Resolve locator to a secure local path, verifying it cannot escape base_dir."""
        if locator.startswith("spill://"):
            # Format: spill://session_id/artifact_id
            parts = locator[len("spill://") :].split("/", 1)
            if len(parts) != 2:
                raise ValidationError(f"Invalid spill URI locator format: {locator}")
            session_id, artifact_id = parts
            session_dir = self._session_dir(session_id)
            target_path = (session_dir / f"{artifact_id}.txt").resolve()
        else:
            target_path = Path(locator).resolve()

        # Strict containment check
        try:
            target_path.relative_to(self.base_dir)
        except ValueError as exc:
            raise ValidationError(
                f"Security violation: Locator '{locator}' attempts to escape spill directory.",
                details={"locator": locator},
            ) from exc

        return target_path

    def save_text(
        self,
        session_id: str,
        content: str,
        *,
        tool_name: str | None = None,
        call_id: str | None = None,
        suggested_name: str | None = None,
    ) -> SpillRef:
        if not session_id or not session_id.strip():
            raise ValidationError("session_id must be provided to save spill text.")

        session_dir = self._session_dir(session_id)
        artifact_id = f"{uuid.uuid4().hex[:12]}_{_sanitize_name(suggested_name or tool_name)}"
        file_path = session_dir / f"{artifact_id}.txt"
        meta_path = session_dir / f"{artifact_id}.meta.json"

        encoded = content.encode("utf-8")
        byte_count = len(encoded)
        char_count = len(content)

        locator = f"spill://{session_id.strip()}/{artifact_id}"
        retrieval_hint = (
            f"Use tool 'spill.slice' or 'spill.fetch' with session_id='{session_id.strip()}' "
            f"and locator='{locator}' plus optional offset/limit to inspect sections."
        )

        ref = SpillRef(
            locator=locator,
            byte_count=byte_count,
            character_count=char_count,
            retrieval_hint=retrieval_hint,
            session_id=session_id.strip(),
            artifact_id=artifact_id,
            tool_name=tool_name,
            call_id=call_id,
            created_at=datetime.now(UTC),
        )

        # Write text and metadata
        file_path.write_text(content, encoding="utf-8")
        meta_path.write_text(ref.model_dump_json(indent=2), encoding="utf-8")
        self._meta_cache[locator] = ref

        return ref

    def read_text(self, locator: str, offset: int = 0, limit: int | None = None) -> str:
        file_path = self._resolve_locator_path(locator)
        if not file_path.is_file():
            raise NotFoundError(
                f"Spill artifact '{locator}' not found on filesystem.",
                details={"locator": locator, "path": str(file_path)},
            )

        content = file_path.read_text(encoding="utf-8")
        if offset < 0:
            offset = 0
        if limit is None:
            return content[offset:]
        return content[offset : offset + limit]

    def get_ref(self, locator: str) -> SpillRef | None:
        if locator in self._meta_cache:
            return self._meta_cache[locator]

        try:
            file_path = self._resolve_locator_path(locator)
            meta_path = file_path.with_suffix(".meta.json")
            if meta_path.is_file():
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                ref = SpillRef.model_validate(data)
                self._meta_cache[locator] = ref
                return ref
        except Exception:
            return None
        return None

    def list_spills(self, session_id: str) -> list[SpillRef]:
        session_dir = self._session_dir(session_id)
        results: list[SpillRef] = []
        for meta_file in session_dir.glob("*.meta.json"):
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                results.append(SpillRef.model_validate(data))
            except Exception:
                continue
        return results


class SpillPolicy:
    """Post-execution result transformer that spills oversized plain-text results."""

    def __init__(
        self,
        store: SpillStore,
        config: SpillPolicyConfig | None = None,
    ) -> None:
        self.store = store
        self.config = config or SpillPolicyConfig()

    def process_tool_result(
        self,
        result: ToolResult,
        *,
        session_id: str | None = None,
        call_id: str | None = None,
    ) -> ToolResult:
        """Inspect ToolResult; if plain-text output exceeds threshold, spill and replace with preview."""
        if not self.config.enabled:
            return result

        if result.tool_name in self.config.excluded_tools:
            return result

        if not result.success or result.output is None:
            return result

        # Convert output to text representation
        text_content: str | None = None
        if isinstance(result.output, str):
            text_content = result.output
        elif isinstance(result.output, (dict, list)):
            text_content = json.dumps(result.output, indent=2, ensure_ascii=False)

        if text_content is None:
            return result

        raw_bytes = len(text_content.encode("utf-8"))
        if raw_bytes <= self.config.max_inline_bytes:
            return result

        if not session_id:
            logger.warning(
                "Tool output exceeds max_inline_bytes but no session_id was provided to spill",
                tool_name=result.tool_name,
                byte_count=raw_bytes,
                max_inline_bytes=self.config.max_inline_bytes,
            )
            return result

        # Best effort attempt to save text
        try:
            spill_ref = self.store.save_text(
                session_id=session_id,
                content=text_content,
                tool_name=result.tool_name,
                call_id=call_id,
            )
        except Exception as exc:
            logger.warning(
                "Failed to save oversized tool output to spill store; falling back to inline result",
                tool_name=result.tool_name,
                error=str(exc),
            )
            return result

        spilled_output = self.compose_bounded_preview(text_content, spill_ref)

        # Update output
        return result.model_copy(
            update={
                "output": spilled_output.formatted_content,
            }
        )

    def compose_bounded_preview(self, content: str, ref: SpillRef) -> SpilledOutput:
        """Compose a head/tail bounded preview and notice staying strictly within max_inline_bytes budget."""
        max_bytes = self.config.max_inline_bytes
        raw_bytes = ref.byte_count

        # Estimate notice size and structural separator overheads ("\n...\n" is 5 bytes, "\n\n" is 2 bytes)
        sample_notice = f"(Omitted {raw_bytes} bytes. Full formatted result stored at: {ref.locator}. {ref.retrieval_hint})"
        notice_bytes = len(sample_notice.encode("utf-8"))
        structural_overhead = 7  # 5 for "\n...\n", 2 for "\n\n"

        remaining_budget = max(0, max_bytes - notice_bytes - structural_overhead)

        if self.config.head_bytes is not None and self.config.tail_bytes is not None:
            head_cap = min(self.config.head_bytes, remaining_budget // 2)
            tail_cap = min(self.config.tail_bytes, remaining_budget - head_cap)
        else:
            head_cap = remaining_budget // 2
            tail_cap = remaining_budget - head_cap

        head_text = _utf8_truncate_head(content, head_cap)
        tail_text = _utf8_truncate_tail(content, tail_cap)

        kept_bytes = len(head_text.encode("utf-8")) + len(tail_text.encode("utf-8"))
        omitted_bytes = max(0, raw_bytes - kept_bytes)

        notice = f"(Omitted {omitted_bytes} bytes. Full formatted result stored at: {ref.locator}. {ref.retrieval_hint})"

        if head_text and tail_text:
            preview = f"{head_text}\n...\n{tail_text}"
            formatted = f"{preview}\n\n{notice}"
        elif head_text:
            preview = f"{head_text}\n..."
            formatted = f"{preview}\n\n{notice}"
        else:
            preview = ""
            formatted = notice

        # Strict safety check: if multi-byte variations caused slight overshoot, trim preview
        while len(formatted.encode("utf-8")) > max_bytes and (head_text or tail_text):
            if len(tail_text) > 0:
                tail_text = tail_text[:-1]
            elif len(head_text) > 0:
                head_text = head_text[:-1]
            kept_bytes = len(head_text.encode("utf-8")) + len(tail_text.encode("utf-8"))
            omitted_bytes = max(0, raw_bytes - kept_bytes)
            notice = f"(Omitted {omitted_bytes} bytes. Full formatted result stored at: {ref.locator}. {ref.retrieval_hint})"
            if head_text and tail_text:
                preview = f"{head_text}\n...\n{tail_text}"
                formatted = f"{preview}\n\n{notice}"
            elif head_text:
                preview = f"{head_text}\n..."
                formatted = f"{preview}\n\n{notice}"
            else:
                preview = ""
                formatted = notice

        return SpilledOutput(
            is_spilled=True,
            preview=preview,
            locator=ref.locator,
            original_bytes=raw_bytes,
            omitted_bytes=omitted_bytes,
            retrieval_hint=ref.retrieval_hint,
            notice=notice,
            formatted_content=formatted,
        )


__all__ = [
    "InMemorySpillStore",
    "LocalFileSpillStore",
    "SpillPolicy",
    "SpillStore",
]
