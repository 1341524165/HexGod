from __future__ import annotations

import json
import hashlib
import os
import shutil
import sys
import time
import traceback
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from PIL import Image

TOOLS_DIR = Path(__file__).resolve().parents[1]
SIDECAR_DIR = Path(__file__).resolve().parent
for candidate in (TOOLS_DIR, SIDECAR_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from augment_pipeline import (
    CaptureSkipped,
    capture_filename_for_mode,
    capture_context_for_image,
    capture_screen,
    run_three_box_ocr,
    write_json,
)
from service import PpOcrOnnxRecognizer, preprocess_image, preprocess_image_in_memory

BALANCED_OCR_BATCH_SIZE = 3
PERFORMANCE_OCR_BATCH_SIZE = 12


def normalize_ocr_performance_mode(value: Any) -> str:
    return "performance" if str(value or "").lower() == "performance" else "balanced"


@dataclass(frozen=True)
class OcrModelSpec:
    model_dir: Path
    model_path: Path
    keys_path: Path
    backend: str
    backend_label: str


def resolve_ocr_model_spec(model_dir: Path) -> OcrModelSpec:
    v5_model = model_dir / "PP-OCRv5_mobile_rec.onnx"
    v5_keys = model_dir / "PP-OCRv5_mobile_rec_keys.txt"
    v5_yml = model_dir / "inference.yml"
    if v5_model.exists() and (v5_keys.exists() or v5_yml.exists()):
        return OcrModelSpec(
            model_dir=model_dir,
            model_path=v5_model,
            keys_path=v5_keys if v5_keys.exists() else v5_yml,
            backend="onnx-v5",
            backend_label="onnx-ppocrv5-rec-worker",
        )

    return OcrModelSpec(
        model_dir=model_dir,
        model_path=model_dir / "ch_PP-OCRv4_rec_int8.onnx",
        keys_path=model_dir / "ppocr_keys_v1.txt",
        backend="onnx",
        backend_label="onnx-ppocr-rec-worker",
    )


def has_ocr_model(candidate: Path) -> bool:
    spec = resolve_ocr_model_spec(candidate)
    return spec.model_path.exists() and spec.keys_path.exists()


def resources_model_dir() -> Path:
    env_dir = os.environ.get("ARAM_ASSISTANT_OCR_MODEL_DIR")
    if env_dir:
        candidate = Path(env_dir)
        if has_ocr_model(candidate):
            return candidate

    root = TOOLS_DIR.parent
    for candidate in (root / "models",):
        if has_ocr_model(candidate):
            return candidate
    return root / "models"


class PersistentOcrRunner:
    def __init__(self) -> None:
        self.recognizer: PpOcrOnnxRecognizer | None = None
        self.model_spec: OcrModelSpec | None = None
        self.load_duration_ms: int | None = None
        self.requests_served = 0
        self.ocr_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self.ocr_cache_hits = 0
        self.ocr_cache_misses = 0
        self.performance_mode = "balanced"
        self.last_inference_batch_size = 0

    def set_performance_mode(self, value: Any) -> str:
        self.performance_mode = normalize_ocr_performance_mode(value)
        return self.performance_mode

    def inference_batch_size(self) -> int:
        if self.performance_mode == "performance":
            return PERFORMANCE_OCR_BATCH_SIZE
        return BALANCED_OCR_BATCH_SIZE

    def ensure_loaded(self) -> bool:
        if self.recognizer is not None:
            return True
        started = time.perf_counter()
        model_dir = resources_model_dir()
        self.model_spec = resolve_ocr_model_spec(model_dir)
        self.recognizer = PpOcrOnnxRecognizer(
            self.model_spec.model_path,
            self.model_spec.keys_path,
        )
        self.load_duration_ms = round((time.perf_counter() - started) * 1000)
        return False

    def warmup(self, preset_key: str) -> Dict[str, Any]:
        started = time.perf_counter()
        was_warm = self.ensure_loaded()
        model_loaded_ms = round((time.perf_counter() - started) * 1000)
        total_ms = round((time.perf_counter() - started) * 1000)
        return {
            "stage": "warmup",
            "source": "",
            "preset": {"key": preset_key},
            "anchor_detection": {
                "detected": False,
                "mean_luma": 0.0,
                "stdev_luma": 0.0,
                "bright_ratio": 0.0,
                "warm_ratio": 0.0,
                "reason": "worker_prewarm_no_capture",
            },
            "ocr_inferred_augment_frame": False,
            "ocr_inference": {
                "detected": False,
                "matched_count": 0,
                "resolved_count": 0,
                "average_confidence": 0.0,
                "slots": [],
            },
            "timings": {
                "total_ms": total_ms,
                "warmup_ms": total_ms,
                "worker_model_load_ms": 0 if was_warm else model_loaded_ms,
                "augment_alias_load_ms": 0,
                "performance_mode": self.performance_mode,
            },
            "slot_candidates": [],
            "variant_budget_used": 0,
            "worker_backend": "worker",
            "frame_quality": {"stage": "warmup"},
            "ocr_warm": True,
            "error": None,
            "payloads": [],
        }

    def __call__(self, image_paths: Sequence[str], backend: str = "onnx") -> List[Dict[str, Any]]:
        was_warm = self.ensure_loaded()
        assert self.recognizer is not None
        backend_label = self.model_spec.backend_label if self.model_spec else "onnx-ppocr-rec-worker"
        payloads: List[Dict[str, Any] | None] = [None] * len(image_paths)
        use_memory_preprocessing = (
            self.performance_mode == "performance"
            and hasattr(self.recognizer, "recognize_image_batch")
        )
        pending: List[
            tuple[int, Path, Path | Image.Image, Path | None, str, bool]
        ] = []
        for index, raw_path in enumerate(image_paths):
            image_path = Path(raw_path).resolve()
            inference_input: Path | Image.Image | None = None
            try:
                if use_memory_preprocessing:
                    inference_input = preprocess_image_in_memory(
                        image_path,
                        grayscale=True,
                        invert=False,
                        max_side=1600,
                    )
                    preprocessed_path = None
                    content_hash = sha256_image(inference_input)
                    preprocess_in_memory = True
                else:
                    inference_input = preprocess_image(
                        image_path,
                        grayscale=True,
                        invert=False,
                        max_side=1600,
                    )
                    preprocessed_path = inference_input
                    content_hash = sha256_file(inference_input)
                    preprocess_in_memory = False
                cached_line = self.ocr_cache.pop(content_hash, None)
                if cached_line is not None:
                    self.ocr_cache[content_hash] = cached_line
                    line = deepcopy(cached_line)
                    self.ocr_cache_hits += 1
                    payloads[index] = {
                        "image": str(image_path),
                        "preprocessed_image": (
                            str(preprocessed_path)
                            if preprocessed_path is not None
                            else None
                        ),
                        "lines": [line],
                        "status": "ok",
                        "backend": backend_label,
                        "error": None,
                        "worker_warm": was_warm,
                        "content_sha256": content_hash,
                        "cache_hit": True,
                        "batch_inference": False,
                        "preprocess_in_memory": preprocess_in_memory,
                    }
                    if isinstance(inference_input, Image.Image):
                        inference_input.close()
                else:
                    self.ocr_cache_misses += 1
                    pending.append(
                        (
                            index,
                            image_path,
                            inference_input,
                            preprocessed_path,
                            content_hash,
                            preprocess_in_memory,
                        )
                    )
            except Exception as err:
                if isinstance(inference_input, Image.Image):
                    inference_input.close()
                payloads[index] = {
                    "image": str(image_path),
                    "preprocessed_image": None,
                    "lines": [],
                    "status": "error",
                    "backend": backend_label,
                    "error": str(err),
                    "worker_warm": was_warm,
                    "cache_hit": False,
                    "batch_inference": False,
                    "preprocess_in_memory": use_memory_preprocessing,
                }

        batch_size = self.inference_batch_size()
        for offset in range(0, len(pending), batch_size):
            batch = pending[offset : offset + batch_size]
            inference_inputs = [item[2] for item in batch]
            in_memory_batch = all(item[5] for item in batch)
            can_batch = (
                hasattr(self.recognizer, "recognize_image_batch")
                if in_memory_batch
                else hasattr(self.recognizer, "recognize_many")
            )
            use_batch = len(batch) > 1 and can_batch
            resolved: List[tuple[Any, Dict[str, Any], bool]] = []
            try:
                if use_batch:
                    try:
                        lines = (
                            self.recognizer.recognize_image_batch(inference_inputs)
                            if in_memory_batch
                            else self.recognizer.recognize_many(inference_inputs)
                        )
                        if len(lines) != len(batch):
                            raise RuntimeError("OCR batch result count mismatch")
                        self.last_inference_batch_size = max(
                            self.last_inference_batch_size,
                            len(batch),
                        )
                        resolved = [
                            (item, line, True)
                            for item, line in zip(batch, lines)
                        ]
                    except Exception:
                        use_batch = False

                if not use_batch:
                    for item in batch:
                        (
                            index,
                            image_path,
                            inference_input,
                            preprocessed_path,
                            content_hash,
                            preprocess_in_memory,
                        ) = item
                        try:
                            line = (
                                self.recognizer.recognize_image(inference_input)
                                if preprocess_in_memory
                                else self.recognizer.recognize(inference_input)
                            )
                            resolved.append((item, line, False))
                        except Exception as err:
                            payloads[index] = {
                                "image": str(image_path),
                                "preprocessed_image": (
                                    str(preprocessed_path)
                                    if preprocessed_path is not None
                                    else None
                                ),
                                "lines": [],
                                "status": "error",
                                "backend": backend_label,
                                "error": str(err),
                                "worker_warm": was_warm,
                                "content_sha256": content_hash,
                                "cache_hit": False,
                                "batch_inference": False,
                                "preprocess_in_memory": preprocess_in_memory,
                            }

                for item, line, batch_inference in resolved:
                    (
                        index,
                        image_path,
                        _inference_input,
                        preprocessed_path,
                        content_hash,
                        preprocess_in_memory,
                    ) = item
                    self.ocr_cache[content_hash] = deepcopy(line)
                    while len(self.ocr_cache) > 128:
                        self.ocr_cache.popitem(last=False)
                    payloads[index] = {
                        "image": str(image_path),
                        "preprocessed_image": (
                            str(preprocessed_path)
                            if preprocessed_path is not None
                            else None
                        ),
                        "lines": [line],
                        "status": "ok",
                        "backend": backend_label,
                        "error": None,
                        "worker_warm": was_warm,
                        "content_sha256": content_hash,
                        "cache_hit": False,
                        "batch_inference": batch_inference,
                        "preprocess_in_memory": preprocess_in_memory,
                    }
            finally:
                for item in batch:
                    if item[5] and isinstance(item[2], Image.Image):
                        item[2].close()
        self.requests_served += 1
        return [payload for payload in payloads if payload is not None]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_image(image: Image.Image) -> str:
    digest = hashlib.sha256()
    digest.update(image.mode.encode("ascii", errors="replace"))
    digest.update(f"{image.width}x{image.height}".encode("ascii"))
    digest.update(image.tobytes())
    return digest.hexdigest()


def empty_soft_skip_response(
    request: Dict[str, Any],
    source: Path,
    reason: str,
    ocr_warm: bool,
    capture_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "request_id": request.get("request_id"),
        "stage": "skipped",
        "source": str(source),
        "anchor_detection": {
            "detected": False,
            "mean_luma": 0.0,
            "stdev_luma": 0.0,
            "bright_ratio": 0.0,
            "warm_ratio": 0.0,
            "reason": reason,
        },
        "ocr_inferred_augment_frame": False,
        "ocr_inference": {
            "detected": False,
            "matched_count": 0,
            "resolved_count": 0,
            "average_confidence": 0.0,
            "slots": [],
        },
        "timings": {"total_ms": 0},
        "slot_candidates": [],
        "variant_budget_used": 0,
        "variant_slots_requested": request.get("variant_slots") or [],
        "worker_backend": "worker",
        "performance_mode": normalize_ocr_performance_mode(request.get("performance_mode")),
        "frame_quality": None,
        "capture_context": capture_context or {},
        "crop_geometry": None,
        "frame_sha256": None,
        "ocr_warm": ocr_warm,
        "payloads": [],
    }


def write_preset_run(output_dir: Path | None, request: Dict[str, Any], payload: Dict[str, Any], started_at: float) -> None:
    if output_dir is None:
        return
    write_json(
        output_dir / "preset-run.json",
        {
            "duration_ms": round((time.perf_counter() - started_at) * 1000),
            "args": {
                "request_id": request.get("request_id"),
                "preset_key": request.get("preset_key"),
                "screen_image": request.get("screen_image"),
                "capture_screen": request.get("screen_image") is None,
                "output_dir": str(output_dir.resolve()),
                "allow_non_lol_foreground": bool(request.get("allow_non_lol_foreground")),
                "worker_backend": "worker",
                "mode": request.get("mode") or "live",
                "performance_mode": normalize_ocr_performance_mode(
                    request.get("performance_mode")
                ),
            },
            "payload": payload,
        },
    )


def handle_request(request: Dict[str, Any], runner: PersistentOcrRunner) -> Dict[str, Any]:
    started = time.perf_counter()
    preset_key = str(request.get("preset_key") or "1920x1080@100")
    performance_mode = runner.set_performance_mode(request.get("performance_mode"))
    runner.last_inference_batch_size = 0
    request_type = request.get("type") or request.get("action")
    if request_type == "warmup":
        payload = runner.warmup(preset_key)
        payload["request_id"] = request.get("request_id")
        timings = dict(payload.get("timings") or {})
        timings["request_total_ms"] = round((time.perf_counter() - started) * 1000)
        payload["timings"] = timings
        return payload

    output_dir = Path(request["output_dir"]).resolve() if request.get("output_dir") else None
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    capture_ms = 0
    if request.get("screen_image"):
        source = Path(str(request["screen_image"])).resolve()
    else:
        capture_target = (
            output_dir or Path.cwd()
        ) / capture_filename_for_mode(performance_mode)
        capture_started = time.perf_counter()
        try:
            source = capture_screen(
                capture_target,
                allow_non_lol_foreground=bool(request.get("allow_non_lol_foreground")),
                performance_mode=performance_mode,
            )
            capture_ms = round((time.perf_counter() - capture_started) * 1000)
        except CaptureSkipped as err:
            capture_ms = round((time.perf_counter() - capture_started) * 1000)
            payload = empty_soft_skip_response(
                request,
                err.target,
                str(err),
                runner.recognizer is not None,
                capture_context_for_image(err.target),
            )
            timings = dict(payload.get("timings") or {})
            request_total_ms = round((time.perf_counter() - started) * 1000)
            timings["capture_ms"] = capture_ms
            timings["pipeline_total_ms"] = 0
            timings["request_total_ms"] = request_total_ms
            timings["total_ms"] = request_total_ms
            payload["timings"] = timings
            write_preset_run(output_dir, request, payload, started)
            return payload
        except RuntimeError as err:
            capture_ms = round((time.perf_counter() - capture_started) * 1000)
            reason = str(err)
            if "未找到 LoL 游戏窗口" in reason or "lol_window_not_found" in reason:
                payload = empty_soft_skip_response(
                    request,
                    capture_target,
                    reason,
                    runner.recognizer is not None,
                    capture_context_for_image(capture_target),
                )
                timings = dict(payload.get("timings") or {})
                request_total_ms = round((time.perf_counter() - started) * 1000)
                timings["capture_ms"] = capture_ms
                timings["pipeline_total_ms"] = 0
                timings["request_total_ms"] = request_total_ms
                timings["total_ms"] = request_total_ms
                payload["timings"] = timings
                write_preset_run(output_dir, request, payload, started)
                return payload
            raise

    was_warm = runner.recognizer is not None
    frame_sha256 = sha256_file(source)
    cache_hits_before = int(getattr(runner, "ocr_cache_hits", 0))
    cache_misses_before = int(getattr(runner, "ocr_cache_misses", 0))
    result = run_three_box_ocr(
        source,
        preset_key,
        output_dir=output_dir,
        backend=str(request.get("backend") or "onnx"),
        ocr_runner=runner,
        mode=str(request.get("mode") or "live"),
        variant_slots=request.get("variant_slots"),
        performance_mode=performance_mode,
    )
    timings = dict(result.get("timings") or {})
    pipeline_total_ms = timings.get("total_ms")
    if isinstance(pipeline_total_ms, (int, float)):
        timings["pipeline_total_ms"] = round(float(pipeline_total_ms))
    timings["capture_ms"] = capture_ms
    request_total_ms = round((time.perf_counter() - started) * 1000)
    timings["request_total_ms"] = request_total_ms
    timings["total_ms"] = request_total_ms
    timings["ocr_cache_hits"] = max(
        0,
        int(getattr(runner, "ocr_cache_hits", 0)) - cache_hits_before,
    )
    timings["ocr_cache_misses"] = max(
        0,
        int(getattr(runner, "ocr_cache_misses", 0)) - cache_misses_before,
    )
    timings["performance_mode"] = performance_mode
    timings["max_inference_batch_size"] = runner.last_inference_batch_size
    timings["preprocess_in_memory"] = performance_mode == "performance"
    timings["source_decode_reused"] = performance_mode == "performance"
    timings["capture_format"] = source.suffix.lower().lstrip(".") or "unknown"
    if runner.load_duration_ms is not None:
        timings.setdefault("worker_model_load_ms", runner.load_duration_ms)
    payload = {
        "request_id": request.get("request_id"),
        "stage": "final",
        "source": str(source),
        "frame_sha256": frame_sha256,
        "preset": result.get("preset"),
        "anchor_detection": result["anchor_detection"],
        "ocr_inferred_augment_frame": result.get("ocr_inferred_augment_frame", False),
        "ocr_inference": result.get("ocr_inference"),
        "timings": timings,
        "slot_candidates": result.get("slot_candidates") or [],
        "variant_budget_used": result.get("variant_budget_used") or 0,
        "variant_slots_requested": result.get("variant_slots_requested") or [],
        "worker_backend": "worker",
        "performance_mode": performance_mode,
        "frame_quality": result.get("frame_quality"),
        "capture_context": (result.get("crop_info") or {}).get("capture_context") or {},
        "crop_geometry": {
            key: (result.get("crop_info") or {}).get(key)
            for key in (
                "image_size",
                "content_viewport",
                "anchor_rect",
                "augment_rects",
                "augment_variant_rects",
            )
            if key in (result.get("crop_info") or {})
        },
        "ocr_warm": was_warm,
        "payloads": result["payloads"],
    }
    capture_policy = str(request.get("capture_policy") or "retain")
    retain_artifacts = (
        capture_policy == "retain"
        or bool(result.get("ocr_inferred_augment_frame"))
        or bool((result.get("frame_quality") or {}).get("probable_augment_frame"))
        or os.environ.get("HEXGOD_OCR_DEBUG", "").lower() in {"1", "true", "yes"}
    )
    payload["artifacts_retained"] = retain_artifacts
    if retain_artifacts:
        write_preset_run(output_dir, request, payload, started)
    elif output_dir is not None:
        shutil.rmtree(output_dir, ignore_errors=True)
    return payload


def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")

    runner = PersistentOcrRunner()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            payload = handle_request(request, runner)
        except Exception as err:
            request_id = None
            try:
                request_id = json.loads(line).get("request_id")
            except Exception:
                pass
            payload = {
                "request_id": request_id,
                "stage": "error",
                "source": "",
                "anchor_detection": {
                    "detected": False,
                    "mean_luma": 0.0,
                    "stdev_luma": 0.0,
                    "bright_ratio": 0.0,
                    "warm_ratio": 0.0,
                    "reason": str(err),
                },
                "ocr_inferred_augment_frame": False,
                "ocr_inference": None,
                "timings": None,
                "slot_candidates": [],
                "variant_budget_used": 0,
                "worker_backend": "worker",
                "performance_mode": getattr(runner, "performance_mode", "balanced"),
                "frame_quality": None,
                "ocr_warm": runner.recognizer is not None,
                "error": f"{err}\n{traceback.format_exc(limit=6)}",
                "payloads": [],
            }
        print(json.dumps(payload, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
