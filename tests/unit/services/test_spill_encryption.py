"""Unit tests for SpillStore at-rest encryption."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.core.security import FernetTokenCipher
from app.domain.errors import ValidationError
from app.domain.models.spill import SpillPolicyConfig
from app.domain.models.tool import ToolExecutionMetadata, ToolResult
from app.services.spill import LocalFileSpillStore, SpillPolicy


@pytest.fixture
def temp_spill_dir(tmp_path: Path) -> Path:
    spill_dir = tmp_path / "spills"
    spill_dir.mkdir(parents=True, exist_ok=True)
    return spill_dir


@pytest.fixture
def cipher() -> FernetTokenCipher:
    key = Fernet.generate_key()
    return FernetTokenCipher(key)


def test_spill_store_encrypts_at_rest(temp_spill_dir: Path, cipher: FernetTokenCipher) -> None:
    """Verify that stored text files on disk are encrypted and not plain text."""
    store = LocalFileSpillStore(temp_spill_dir, cipher=cipher)
    secret_text = "CONFIDENTIAL: user personal sensitive data 123456789"
    session_id = "session-enc-test"

    ref = store.save_text(session_id, secret_text, tool_name="test_tool")

    # Resolve physical path on disk
    target_path = store._resolve_locator_path(ref.locator)
    assert target_path.exists()

    raw_disk_content = target_path.read_text(encoding="utf-8")
    # Raw content must NOT contain the plaintext secret
    assert secret_text not in raw_disk_content
    # Decrypt should restore original plaintext
    decrypted = cipher.decrypt(raw_disk_content)
    assert decrypted == secret_text

    # read_text should automatically decrypt
    assert store.read_text(ref.locator) == secret_text
    # read_text slicing works
    assert store.read_text(ref.locator, offset=0, limit=12) == "CONFIDENTIAL"


def test_spill_store_backward_compatibility_unencrypted(
    temp_spill_dir: Path, cipher: FernetTokenCipher
) -> None:
    """Verify that unencrypted legacy files are read gracefully without breaking."""
    store = LocalFileSpillStore(temp_spill_dir, cipher=cipher)
    legacy_text = "Legacy unencrypted file content"
    session_id = "session-legacy"

    # Manually write an unencrypted file
    ref = store.save_text(session_id, "placeholder", tool_name="legacy")
    target_path = store._resolve_locator_path(ref.locator)
    target_path.write_text(legacy_text, encoding="utf-8")

    # Legacy plaintext must not be returned when a cipher is configured (L1).
    with pytest.raises(ValidationError, match="Unable to decrypt"):
        store.read_text(ref.locator)


def test_spill_policy_with_encrypted_store(temp_spill_dir: Path, cipher: FernetTokenCipher) -> None:
    """Verify SpillPolicy integrates seamlessly with encrypted storage."""
    store = LocalFileSpillStore(temp_spill_dir, cipher=cipher)
    policy = SpillPolicy(store=store, config=SpillPolicyConfig(max_inline_bytes=64))

    long_output = "Line " + "x" * 100 + " End"
    tool_result = ToolResult(
        tool_name="web_search",
        success=True,
        output=long_output,
        metadata=ToolExecutionMetadata(
            tool_name="web_search",
            latency_ms=10.0,
            timestamp=datetime.now(UTC),
        ),
    )
    processed = policy.process_tool_result(tool_result, session_id="session-policy-test")

    # Result output contains spill notice
    assert "spill://" in str(processed.output)

    # Verify content read from store matches original
    spills = store.list_spills("session-policy-test")
    assert len(spills) == 1
    loaded = store.read_text(spills[0].locator)
    assert loaded == long_output


def test_spill_decrypt_failure_is_fail_closed(
    temp_spill_dir: Path, cipher: FernetTokenCipher
) -> None:
    store = LocalFileSpillStore(temp_spill_dir, cipher=cipher)
    ref = store.save_text("session-enc-test", "secret-spill", tool_name="test_tool")
    target_path = store._resolve_locator_path(ref.locator)
    target_path.write_text("not-a-fernet-token", encoding="utf-8")
    with pytest.raises(ValidationError, match="Unable to decrypt"):
        store.read_text(ref.locator)
