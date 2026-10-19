from __future__ import annotations

import argparse
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence

import numpy as np
from PIL import Image, ImageOps
from hexgod.paths import MODEL_DIR


def maybe_build_engine(lang: str):
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except Exception:
        return None, "paddleocr not installed"

    try:
        engine = PaddleOCR(use_angle_cls=True, lang=lang)
        return engine, None
    except Exception as err:
        return None, str(err)


def extract_character_dict_from_inference_yml(path: Path) -> List[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"(?ms)^\s*character_dict:\s*\n(?P<body>(?:^\s*-\s.*\n?)+)",
        text,
    )
    if not match:
        raise ValueError(f"character_dict not found: {path}")
    characters: List[str] = []
    for raw_line in match.group("body").splitlines():
        item_match = re.match(r"^\s*-\s?(.*)$", raw_line)
        if not item_match:
            continue
        value = item_match.group(1)
        if "#" in value:
            value = value.split("#", 1)[0]
        value = value.strip(" \t\r")
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        characters.append(value)
    if not characters:
        raise ValueError(f"empty character_dict: {path}")
    return characters


def load_character_dict(path: Path) -> List[str]:
    if path.suffix.lower() in (".yml", ".yaml"):
        return extract_character_dict_from_inference_yml(path)
    return path.read_text(encoding="utf-8").splitlines()


class PpOcrOnnxRecognizer:
    def __init__(self, model_path: Path, keys_path: Path, img_height: int = 48, img_width: int = 320):
        import onnxruntime as ort  # type: ignore

        self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.img_height = img_height
        self.img_width = img_width
        keys = load_character_dict(keys_path)
        self.characters = [""] + keys

    def recognize(self, image_path: Path) -> Dict[str, Any]:
        return self.recognize_many([image_path])[0]

    def recognize_many(self, image_paths: List[Path]) -> List[Dict[str, Any]]:
        images: List[Image.Image] = []
        try:
            for image_path in image_paths:
                image = Image.open(image_path).convert("RGB")
                image.load()
                images.append(image)
            return self.recognize_image_batch(images)
        finally:
            for image in images:
                image.close()

    def recognize_image(self, image: Image.Image) -> Dict[str, Any]:
        return self.recognize_image_batch([image])[0]

    def recognize_image_batch(self, images: Sequence[Image.Image]) -> List[Dict[str, Any]]:
        if not images:
            return []
        batch = np.concatenate(
            [self._preprocess_image(image) for image in images],
            axis=0,
        )
        output = self.session.run(None, {self.input_name: batch})[0]
        return [self._decode(output[index]) for index in range(len(images))]

    def _decode(self, output: np.ndarray) -> Dict[str, Any]:
        indices = output.argmax(axis=1)
        probs = output.max(axis=1)
        chars: List[str] = []
        scores: List[float] = []
        prev_idx = None
        for idx, prob in zip(indices.tolist(), probs.tolist()):
            if idx != 0 and idx != prev_idx and idx < len(self.characters):
                chars.append(self.characters[idx])
                scores.append(float(prob))
            prev_idx = idx

        text = "".join(chars)
        score = float(sum(scores) / len(scores)) if scores else 0.0
        return {"text": text, "score": score}

    def _preprocess(self, image_path: Path) -> np.ndarray:
        with Image.open(image_path) as raw_image:
            image = raw_image.convert("RGB")
            image.load()
        try:
            return self._preprocess_image(image)
        finally:
            image.close()

    def _preprocess_image(self, image: Image.Image) -> np.ndarray:
        image = image.convert("RGB")
        width, height = image.size
        ratio = width / float(height if height else 1)
        resized_w = min(self.img_width, max(1, int(math.ceil(self.img_height * ratio))))
        resized = image.resize((resized_w, self.img_height), Image.Resampling.BILINEAR)

        arr = np.asarray(resized).astype("float32") / 255.0
        arr = arr.transpose(2, 0, 1)
        arr = (arr - 0.5) / 0.5

        padded = np.zeros((3, self.img_height, self.img_width), dtype=np.float32)
        padded[:, :, :resized_w] = arr
        return padded[np.newaxis, :]


def maybe_build_onnx_engine(model_path: Path, keys_path: Path):
    try:
        import onnxruntime  # type: ignore  # noqa: F401
    except Exception:
        return None, "onnxruntime not installed"

    if not model_path.exists():
        return None, f"rec model not found: {model_path}"
    if not keys_path.exists():
        return None, f"keys file not found: {keys_path}"

    try:
        return PpOcrOnnxRecognizer(model_path, keys_path), None
    except Exception as err:
        return None, str(err)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OCR sidecar.")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--images", nargs="+", type=Path)
    parser.add_argument("--lang", default="ch")
    parser.add_argument("--grayscale", action="store_true")
    parser.add_argument("--invert", action="store_true")
    parser.add_argument("--max-side", type=int, default=0)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--backend", choices=["auto", "mock", "paddle", "onnx", "onnx-v5"], default="auto")
    parser.add_argument("--rec-model", type=Path)
    parser.add_argument("--keys-path", type=Path)
    parser.add_argument(
        "--performance-mode",
        choices=["balanced", "performance"],
        default="balanced",
    )
    return parser.parse_args()


def preprocess_image_in_memory(
    path: Path,
    grayscale: bool,
    invert: bool,
    max_side: int,
) -> Image.Image:
    with Image.open(path) as raw_image:
        image = raw_image.copy()
    if grayscale:
        image = ImageOps.grayscale(image)
    if invert:
        image = ImageOps.invert(image.convert("L"))
    if max_side > 0:
        width, height = image.size
        longest = max(width, height)
        if longest > max_side:
            ratio = max_side / longest
            image = image.resize((int(width * ratio), int(height * ratio)))

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    image.load()
    return image


def preprocess_image(path: Path, grayscale: bool, invert: bool, max_side: int) -> Path:
    image = preprocess_image_in_memory(path, grayscale, invert, max_side)

    target = path.with_name(f"{path.stem}.preprocessed.png")
    try:
        image.save(target)
    finally:
        image.close()
    return target


def mock_payload(args: argparse.Namespace, status: str = "mock", image_path: Path | None = None) -> Dict[str, Any]:
    image = image_path or args.image
    return {
        "image": str(image),
        "preprocessed_image": None,
        "lines": [
            {"text": "巫师式思考", "score": 0.98},
        ],
        "status": status,
        "backend": "mock",
        "error": None,
    }


def resolve_model_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    default_base = Path(os.environ.get("ARAM_ASSISTANT_OCR_MODEL_DIR", str(MODEL_DIR)))
    if args.backend == "onnx-v5":
        rec_model = args.rec_model or (default_base / "PP-OCRv5_mobile_rec.onnx")
        keys_path = args.keys_path or (default_base / "PP-OCRv5_mobile_rec_keys.txt")
        if not keys_path.exists() and (default_base / "inference.yml").exists():
            keys_path = default_base / "inference.yml"
    else:
        rec_model = args.rec_model or (default_base / "ch_PP-OCRv4_rec_int8.onnx")
        keys_path = args.keys_path or (default_base / "ppocr_keys_v1.txt")
    return rec_model, keys_path


def run_paddle(engine: Any, image_path: Path) -> List[Dict[str, Any]]:
    raw = engine.ocr(str(image_path), cls=True)
    lines: List[Dict[str, Any]] = []
    for block in raw or []:
        for item in block or []:
            if len(item) < 2:
                continue
            text = item[1][0]
            score = float(item[1][1])
            lines.append({"text": text, "score": score})
    lines.sort(key=lambda item: item["score"], reverse=True)
    return lines[:1]


def main() -> None:
    # Force UTF-8 stdout on Windows so Rust can parse the JSON correctly
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    image_paths = args.images or ([args.image] if args.image else [])
    image_paths = [path.resolve() for path in image_paths]
    if not image_paths:
        payload = mock_payload(args, "error")
        payload["error"] = "missing --image or --images"
        print(json.dumps(payload, ensure_ascii=False))
        return
    args.image = image_paths[0]

    def emit(payloads: List[Dict[str, Any]]) -> None:
        if len(payloads) == 1:
            print(json.dumps(payloads[0], ensure_ascii=False))
        else:
            print(json.dumps({"payloads": payloads}, ensure_ascii=False))

    if args.mock:
        emit([mock_payload(args, image_path=image_path) for image_path in image_paths])
        return

    missing = [path for path in image_paths if not path.exists()]
    if missing:
        payload = mock_payload(args, "error", missing[0])
        payload["error"] = f"image not found: {missing[0]}"
        print(json.dumps(payload, ensure_ascii=False))
        return

    preprocessed_paths = [
        preprocess_image(path, grayscale=args.grayscale, invert=args.invert, max_side=args.max_side)
        for path in image_paths
    ]

    if args.backend in ("auto", "paddle"):
        engine, err = maybe_build_engine(args.lang)
        if engine is not None:
            try:
                payloads = []
                for image_path, preprocessed in zip(image_paths, preprocessed_paths):
                    payloads.append(
                        {
                            "image": str(image_path),
                            "preprocessed_image": str(preprocessed),
                            "lines": run_paddle(engine, preprocessed),
                            "status": "ok",
                            "backend": "paddleocr",
                            "error": None,
                        }
                    )
                emit(payloads)
                return
            except Exception as ocr_err:
                if args.backend == "paddle":
                    payload = mock_payload(args, "degraded")
                    payload["preprocessed_image"] = str(preprocessed_paths[0])
                    payload["error"] = str(ocr_err)
                    payload["backend"] = "mock"
                    print(json.dumps(payload, ensure_ascii=False))
                    return
        elif args.backend == "paddle":
            payload = mock_payload(args, "degraded")
            payload["preprocessed_image"] = str(preprocessed_paths[0])
            payload["error"] = err
            payload["backend"] = "mock"
            print(json.dumps(payload, ensure_ascii=False))
            return

    if args.backend in ("auto", "onnx", "onnx-v5"):
        rec_model, keys_path = resolve_model_paths(args)
        recognizer, err = maybe_build_onnx_engine(rec_model, keys_path)
        if recognizer is not None:
            try:
                recognized_lines = (
                    recognizer.recognize_many(preprocessed_paths)
                    if args.performance_mode == "performance"
                    else [recognizer.recognize(path) for path in preprocessed_paths]
                )
                payloads = []
                for image_path, preprocessed, line in zip(
                    image_paths,
                    preprocessed_paths,
                    recognized_lines,
                ):
                    payloads.append(
                        {
                            "image": str(image_path),
                            "preprocessed_image": str(preprocessed),
                            "lines": [line],
                            "status": "ok",
                            "backend": "onnx-ppocrv5-rec" if args.backend == "onnx-v5" else "onnx-ppocr-rec",
                            "error": None,
                        }
                    )
                emit(payloads)
                return
            except Exception as ocr_err:
                if args.backend in ("onnx", "onnx-v5"):
                    payload = mock_payload(args, "degraded")
                    payload["preprocessed_image"] = str(preprocessed_paths[0])
                    payload["error"] = str(ocr_err)
                    payload["backend"] = "mock"
                    print(json.dumps(payload, ensure_ascii=False))
                    return
        elif args.backend in ("onnx", "onnx-v5"):
            payload = mock_payload(args, "degraded")
            payload["preprocessed_image"] = str(preprocessed_paths[0])
            payload["error"] = err
            payload["backend"] = "mock"
            print(json.dumps(payload, ensure_ascii=False))
            return

    payload = mock_payload(args, "degraded")
    payload["preprocessed_image"] = str(preprocessed_paths[0])
    payload["error"] = "no OCR backend available"
    payload["backend"] = "mock"

    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
