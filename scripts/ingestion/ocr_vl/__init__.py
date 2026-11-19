"""Offline OCR with the fine-tuned PaddleOCR-VL checkpoint (round 11 and later).

A port of the scan path of the OCR web app (``~/namdv/ocr/server.py`` on the GPU server):
PP-DocLayoutV2 finds blocks, the fine-tuned VL model reads each block with its task prompt,
and the blocks are written as Markdown. Used by ``scripts/ingestion/ocr_batch.py``; never
imported by the assistant runtime. Paddle, torch and transformers load lazily, so the pure
parts (layout clean-up, Markdown, table split, batching plan) are tested without a GPU.
"""

from .checkpoint import CheckpointIdentity, describe_checkpoint, validate_checkpoint
from .pipeline import PIPELINE_VERSION, PageText, VlPipeline

__all__ = [
    "PIPELINE_VERSION",
    "CheckpointIdentity",
    "PageText",
    "VlPipeline",
    "describe_checkpoint",
    "validate_checkpoint",
]
