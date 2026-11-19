"""Identity of a local VL checkpoint: a readable name and a content hash.

The hash covers every ``*.safetensors`` file, so the sidecar's ``engine_version`` (and with it
the ingestion fingerprint) changes whenever the weights change, wherever the folder lives.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

GENERIC_DIR_NAMES = frozenset({"ckpt", "checkpoint", "model", "weights"})
_READ_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class CheckpointIdentity:
    name: str
    sha256: str

    def short(self) -> str:
        return f"{self.name} sha256:{self.sha256[:12]}"


def weight_files(model_path: Path) -> list[Path]:
    return sorted(model_path.glob("*.safetensors"))


def validate_checkpoint(model_path: Path) -> None:
    """Raise ``ValueError`` unless the folder looks like a transformers checkpoint."""
    if not model_path.is_dir():
        raise ValueError(f"model path is not a directory: {model_path}")
    if not (model_path / "config.json").is_file():
        raise ValueError(f"no config.json in model path: {model_path}")
    if not weight_files(model_path):
        raise ValueError(f"no *.safetensors weights in model path: {model_path}")


def default_model_name(model_path: Path) -> str:
    """Folder name, or its parent's when the folder has a generic name such as ``ckpt``."""
    resolved = model_path.resolve()
    if resolved.name.lower() in GENERIC_DIR_NAMES and resolved.parent.name:
        return resolved.parent.name
    return resolved.name


def checkpoint_digest(model_path: Path) -> str:
    """SHA-256 over the names and bytes of every weight file, streamed."""
    digest = hashlib.sha256()
    for path in weight_files(model_path):
        digest.update(path.name.encode())
        with path.open("rb") as handle:
            while chunk := handle.read(_READ_CHUNK):
                digest.update(chunk)
    return digest.hexdigest()


def describe_checkpoint(model_path: Path, name: str | None = None) -> CheckpointIdentity:
    validate_checkpoint(model_path)
    return CheckpointIdentity(
        name=name or default_model_name(model_path), sha256=checkpoint_digest(model_path)
    )
