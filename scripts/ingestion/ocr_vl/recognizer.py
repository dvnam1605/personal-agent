"""The fine-tuned PaddleOCR-VL model, loaded from a local transformers checkpoint.

The checkpoint ships its own model code (``trust_remote_code``). Each block is read with the
prompt of its task, greedy decoding, the same resolution cap as the evaluation runs
(``max_pixels`` = 2 822 400). The GPU is used the way the OCR web app measured fastest:

- blocks are batched; decoding is overhead-bound, so eight short blocks cost about one;
- a batch lasts as long as its longest output, so every table/chart batch also carries the
  largest text blocks;
- on CUDA out-of-memory a batch is split in halves, and a single huge block (a full-page table)
  is re-read at 60/40/25 % resolution instead of failing;
- an optional VRAM cap keeps the process inside a share of a GPU used by other jobs.

Torch and transformers are imported only when the model is first used.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PIL.Image import Image

PROMPT = {
    "ocr": "OCR:",
    "table": "Table Recognition:",
    "formula": "Formula Recognition:",
    "chart": "Chart Recognition:",
}
MAX_NEW_TOKENS = {"ocr": 1024, "table": 4096, "formula": 1024, "chart": 2048}
MAX_PIXELS = 2_822_400
LONG_TASKS = frozenset({"table", "chart"})
BATCH_SHORT = 8
BATCH_LONG = 2
FILL = 6
SHRINK_STEPS = (0.6, 0.4, 0.25)


def plan_batches(items: Sequence[tuple[int, str]]) -> list[list[int]]:
    """``(area, task)`` per block -> batches of block indices, largest first.

    Each batch with up to two tables/charts also takes the next six largest text blocks; the
    remaining text blocks go eight per batch.
    """
    long_ = sorted(
        (k for k, (_, task) in enumerate(items) if task in LONG_TASKS),
        key=lambda k: items[k][0],
        reverse=True,
    )
    short = sorted(
        (k for k, (_, task) in enumerate(items) if task not in LONG_TASKS),
        key=lambda k: items[k][0],
        reverse=True,
    )
    batches: list[list[int]] = []
    for start in range(0, len(long_), BATCH_LONG):
        batches.append(long_[start : start + BATCH_LONG] + short[:FILL])
        short = short[FILL:]
    batches.extend(
        short[start : start + BATCH_SHORT] for start in range(0, len(short), BATCH_SHORT)
    )
    return batches


class VlRecognizer:
    """Reads cropped blocks with the local checkpoint on one device (``cuda:N`` or ``cpu``)."""

    def __init__(self, model_path: Path, device: str, vram_gb: float | None = None) -> None:
        self.model_path = model_path
        self.device = device
        self.vram_gb = vram_gb
        self._model: Any = None
        self._processor: Any = None

    @property
    def on_cuda(self) -> bool:
        return self.device.startswith("cuda")

    def load(self) -> None:
        """Load model + processor once; bf16 on Ampere or newer, fp16 on older GPUs, fp32 on CPU."""
        if self._model is not None:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor

        dtype = torch.float32
        if self.on_cuda:
            device = torch.device(self.device)
            if self.vram_gb:
                total = torch.cuda.get_device_properties(device).total_memory / 2**30
                torch.cuda.set_per_process_memory_fraction(min(1.0, self.vram_gb / total), device)
            major, _ = torch.cuda.get_device_capability(device)
            dtype = torch.bfloat16 if major >= 8 else torch.float16
        # Local checkpoint directory (not a Hub download); its model code is the user's own fine-tune.
        model = AutoModelForCausalLM.from_pretrained(  # nosec B615
            str(self.model_path),
            trust_remote_code=True,
            torch_dtype=dtype,
            attn_implementation="sdpa",
        )
        model = model.to(self.device).eval()
        model.visual.config._attn_implementation = "sdpa"
        processor = AutoProcessor.from_pretrained(  # nosec B615
            str(self.model_path), trust_remote_code=True
        )
        processor.image_processor.max_pixels = MAX_PIXELS
        processor.tokenizer.padding_side = "left"
        self._model, self._processor = model, processor

    def read(self, items: Sequence[tuple[Image, str]]) -> list[str]:
        """``(crop, task)`` pairs -> recognised text, in the same order."""
        if not items:
            return []
        self.load()
        out = [""] * len(items)
        plan = plan_batches([(image.width * image.height, task) for image, task in items])
        for batch in plan:
            texts = self._run_batch([items[k] for k in batch])
            for k, text in zip(batch, texts, strict=True):
                out[k] = text
        return out

    def release_cache(self) -> None:
        """Give cached CUDA blocks back between page groups so the process stays near model size."""
        if self.on_cuda:
            import torch

            torch.cuda.empty_cache()

    def _generate(self, items: Sequence[tuple[Image, str]]) -> list[str]:
        import torch

        processor, model = self._processor, self._model
        texts = [
            processor.apply_chat_template(
                [
                    {
                        "role": "user",
                        "content": [{"type": "image"}, {"type": "text", "text": PROMPT[task]}],
                    }
                ],
                tokenize=False,
                add_generation_prompt=True,
            )
            for _, task in items
        ]
        inputs = processor(
            text=texts, images=[image for image, _ in items], padding=True, return_tensors="pt"
        ).to(self.device)
        with torch.inference_mode():
            output = model.generate(
                **inputs,
                max_new_tokens=max(MAX_NEW_TOKENS[task] for _, task in items),
                do_sample=False,
                use_cache=True,
            )
        new_tokens = output[:, inputs["input_ids"].shape[1] :]
        return [
            text.strip() for text in processor.batch_decode(new_tokens, skip_special_tokens=True)
        ]

    def _try(self, items: Sequence[tuple[Image, str]]) -> list[str] | None:
        """None on CUDA OOM; the retry happens outside the ``except`` so the failed activations are freed."""
        import torch

        try:
            return self._generate(items)
        except torch.cuda.OutOfMemoryError:
            return None

    def _run_batch(self, items: Sequence[tuple[Image, str]]) -> list[str]:
        out = self._try(items)
        if out is not None:
            return out
        self.release_cache()
        if len(items) > 1:
            half = len(items) // 2
            return self._run_batch(items[:half]) + self._run_batch(items[half:])
        image_processor = self._processor.image_processor
        try:
            for factor in SHRINK_STEPS:
                image_processor.max_pixels = int(MAX_PIXELS * factor)
                out = self._try(items)
                if out is not None:
                    return out
                self.release_cache()
        finally:
            image_processor.max_pixels = MAX_PIXELS
        raise RuntimeError("block too large for the GPU even at 25% resolution")
