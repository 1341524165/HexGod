from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from augment_pipeline import (
    CaptureSkipped,
    capture_filename_for_mode,
    capture_context_for_image,
    capture_screen,
    run_ocr_images,
    run_three_box_ocr,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run three-box augment OCR from a preset.")
    parser.add_argument("--preset-key", required=True)
    parser.add_argument("--screen-image", type=Path)
    parser.add_argument("--capture-screen", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--allow-non-lol-foreground", action="store_true")
    parser.add_argument("--mode", choices=["live", "diagnostic"], default="live")
    parser.add_argument("--variant-slots", nargs="*", type=int)
    parser.add_argument(
        "--performance-mode",
        choices=["balanced", "performance"],
        default="balanced",
    )
    return parser.parse_args()


def run_ocr_batch(
    image_paths: list[str],
    backend: str,
    performance_mode: str = "balanced",
) -> list[dict]:
    return run_ocr_images(
        [Path(image_path) for image_path in image_paths],
        backend=backend,
        performance_mode=performance_mode,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_preset_run(args: argparse.Namespace, payload: dict, started_at: float) -> None:
    if not args.output_dir:
        return
    write_json(
        args.output_dir / "preset-run.json",
        {
            "duration_ms": round((time.perf_counter() - started_at) * 1000),
            "args": {
                "preset_key": args.preset_key,
                "screen_image": str(args.screen_image.resolve()) if args.screen_image else None,
                "capture_screen": bool(args.capture_screen),
                "output_dir": str(args.output_dir.resolve()),
                "allow_non_lol_foreground": bool(args.allow_non_lol_foreground),
                "mode": args.mode,
                "variant_slots": args.variant_slots,
                "performance_mode": args.performance_mode,
            },
            "payload": payload,
        },
    )


def main() -> None:
    # Force UTF-8 stdout on Windows so Rust can parse the JSON correctly
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    started_at = time.perf_counter()
    if args.screen_image:
        source = args.screen_image.resolve()
    else:
        output_dir = args.output_dir or (
            Path(__file__).resolve().parents[1] / "resources" / "captures" / args.preset_key / "preset-ocr"
        )
        try:
            source = capture_screen(
                output_dir / capture_filename_for_mode(args.performance_mode),
                allow_non_lol_foreground=args.allow_non_lol_foreground,
                performance_mode=args.performance_mode,
            )
        except CaptureSkipped as error:
            payload = {
                "source": str(error.target),
                "anchor_detection": {
                    "detected": False,
                    "mean_luma": 0.0,
                    "stdev_luma": 0.0,
                    "bright_ratio": 0.0,
                    "warm_ratio": 0.0,
                    "reason": str(error),
                },
                "ocr_inferred_augment_frame": False,
                "ocr_inference": {
                    "detected": False,
                    "matched_count": 0,
                    "resolved_count": 0,
                    "average_confidence": 0.0,
                    "slots": [],
                },
                "payloads": [],
                "timings": {"total_ms": round((time.perf_counter() - started_at) * 1000)},
                "slot_candidates": [],
                "variant_budget_used": 0,
                "variant_slots_requested": args.variant_slots or [],
                "worker_backend": "cli",
                "performance_mode": args.performance_mode,
                "frame_quality": None,
                "capture_context": capture_context_for_image(error.target),
                "crop_geometry": None,
                "frame_sha256": None,
                "ocr_warm": False,
            }
            write_preset_run(args, payload, started_at)
            print(json.dumps(payload, ensure_ascii=False))
            return

    result = run_three_box_ocr(
        source,
        args.preset_key,
        output_dir=args.output_dir,
        ocr_runner=lambda paths, backend: run_ocr_batch(
            paths,
            backend,
            args.performance_mode,
        ),
        mode=args.mode,
        variant_slots=args.variant_slots,
        performance_mode=args.performance_mode,
    )
    crop_info = result.get("crop_info") or {}
    payload = {
        "source": str(source),
        "frame_sha256": sha256_file(source),
        "preset": result["preset"],
        "anchor_detection": result["anchor_detection"],
        "ocr_inferred_augment_frame": result.get("ocr_inferred_augment_frame", False),
        "ocr_inference": result.get("ocr_inference"),
        "timings": result.get("timings"),
        "slot_candidates": result.get("slot_candidates") or [],
        "variant_budget_used": result.get("variant_budget_used") or 0,
        "variant_slots_requested": result.get("variant_slots_requested") or [],
        "worker_backend": "cli",
        "performance_mode": args.performance_mode,
        "frame_quality": result.get("frame_quality"),
        "capture_context": crop_info.get("capture_context") or {},
        "crop_geometry": {
            key: crop_info.get(key)
            for key in (
                "image_size",
                "content_viewport",
                "anchor_rect",
                "augment_rects",
                "augment_variant_rects",
            )
            if key in crop_info
        },
        "ocr_warm": False,
        "payloads": result["payloads"],
    }
    write_preset_run(args, payload, started_at)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
