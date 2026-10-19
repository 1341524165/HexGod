from __future__ import annotations
from hexgod.common import normalize_text

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from PIL import Image, ImageGrab, ImageOps, ImageStat

_DPI_AWARENESS_SET = False
DEBUG_PNG_COMPRESS_LEVEL = 1
PERFORMANCE_PNG_COMPRESS_LEVEL = 0
OCR_PERFORMANCE_BALANCED = "balanced"
OCR_PERFORMANCE_HIGH = "performance"
ULTRAWIDE_UI_MIN_ASPECT_RATIO = 3.0
LOL_UI_ASPECT_WIDTH = 16
LOL_UI_ASPECT_HEIGHT = 9


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def to_bbox(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.width, self.y + self.height)

    def to_dict(self) -> Dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    def scaled(self, scale_x: float, scale_y: float) -> "Rect":
        return Rect(
            x=int(round(self.x * scale_x)),
            y=int(round(self.y * scale_y)),
            width=max(1, int(round(self.width * scale_x))),
            height=max(1, int(round(self.height * scale_y))),
        )

    def shifted_expanded(self, dx: int, dy: int, dw: int, dh: int) -> "Rect":
        return Rect(
            x=self.x + dx,
            y=self.y + dy,
            width=max(1, self.width + dw),
            height=max(1, self.height + dh),
        )


@dataclass(frozen=True)
class AugmentPreset:
    key: str
    width: int
    height: int
    scale: int
    anchor_rect: Rect
    augment_rects: List[Rect]
    content_viewport: Optional[Rect] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "key": self.key,
            "width": self.width,
            "height": self.height,
            "scale": self.scale,
            "anchor_rect": self.anchor_rect.to_dict(),
            "augment_rects": [rect.to_dict() for rect in self.augment_rects],
        }
        if self.content_viewport is not None:
            payload["content_viewport"] = self.content_viewport.to_dict()
        return payload

    def scaled_to(self, width: int, height: int) -> "AugmentPreset":
        scale_x = width / float(self.width)
        scale_y = height / float(self.height)
        return AugmentPreset(
            key=f"{self.key}->scaled:{width}x{height}",
            width=width,
            height=height,
            scale=self.scale,
            anchor_rect=self.anchor_rect.scaled(scale_x, scale_y),
            augment_rects=[rect.scaled(scale_x, scale_y) for rect in self.augment_rects],
            content_viewport=Rect(x=0, y=0, width=width, height=height),
        )

    def scaled_to_viewport(
        self,
        image_width: int,
        image_height: int,
        viewport: Rect,
    ) -> "AugmentPreset":
        scale_x = viewport.width / float(self.width)
        scale_y = viewport.height / float(self.height)

        def transform(rect: Rect) -> Rect:
            scaled = rect.scaled(scale_x, scale_y)
            return Rect(
                x=viewport.x + scaled.x,
                y=viewport.y + scaled.y,
                width=scaled.width,
                height=scaled.height,
            )

        return AugmentPreset(
            key=(
                f"{self.key}->viewport:"
                f"{viewport.x},{viewport.y},{viewport.width}x{viewport.height}"
            ),
            width=image_width,
            height=image_height,
            scale=self.scale,
            anchor_rect=transform(self.anchor_rect),
            augment_rects=[transform(rect) for rect in self.augment_rects],
            content_viewport=viewport,
        )


@dataclass
class AnchorDetection:
    detected: bool
    mean_luma: float
    stdev_luma: float
    bright_ratio: float
    warm_ratio: float
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AutoWatcher:
    def __init__(self) -> None:
        self.state = "idle"
        self.last_signature: Optional[tuple[str, ...]] = None
        self.last_non_empty_signature: Optional[tuple[str, ...]] = None
        self.consecutive_matches = 0
        self.failures = 0
        self.stable_result: Optional[Dict[str, Any]] = None
        self.history: List[Dict[str, Any]] = []

    def observe(self, frame_index: int, cycle_result: Dict[str, Any]) -> None:
        anchor_detected = bool(cycle_result.get("anchor_detection", {}).get("detected"))
        matched_names = tuple(
            item["matched_name"]
            for item in cycle_result.get("ocr_matches", [])
            if item.get("matched_name")
        )
        all_frames_matched = bool(matched_names) and all(
            item.get("mode") in ("exact", "fuzzy") for item in cycle_result.get("ocr_matches", [])
        )

        if not anchor_detected:
            self.state = "watching_anchor"
            self.failures += 1
            self.history.append(
                {
                    "frame_index": frame_index,
                    "state": self.state,
                    "anchor_detected": False,
                    "signature": [],
                }
            )
            return

        self.state = "capturing"
        if not all_frames_matched:
            self.state = "matching_done"
            self.failures += 1
            self.last_signature = matched_names if matched_names else None
            self.history.append(
                {
                    "frame_index": frame_index,
                    "state": self.state,
                    "anchor_detected": True,
                    "signature": list(matched_names),
                }
            )
            return

        if matched_names == self.last_non_empty_signature:
            self.consecutive_matches += 1
        else:
            self.last_non_empty_signature = matched_names
            self.consecutive_matches = 1

        self.state = "matching_done"
        if self.consecutive_matches >= 2:
            self.state = "stable_result_ready"
            self.stable_result = cycle_result

        self.history.append(
            {
                "frame_index": frame_index,
                "state": self.state,
                "anchor_detected": True,
                "signature": list(matched_names),
                "consecutive_matches": self.consecutive_matches,
            }
        )


def project_root() -> Path:
    from hexgod.paths import PROJECT_ROOT
    return PROJECT_ROOT


def resources_data_dir() -> Path:
    from hexgod.paths import DATA_DIR
    env_dir = os.environ.get("ARAM_ASSISTANT_DATA_DIR")
    return Path(env_dir) if env_dir else DATA_DIR


def resources_model_dir() -> Path:
    from hexgod.paths import MODEL_DIR
    env_dir = os.environ.get("ARAM_ASSISTANT_OCR_MODEL_DIR")
    if env_dir and (Path(env_dir) / "ch_PP-OCRv4_rec_int8.onnx").exists():
        return Path(env_dir)
    return MODEL_DIR


def presets_path() -> Path:
    from hexgod.paths import PRESET_PATH
    return PRESET_PATH


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def save_png_atomic(
    image: Image.Image,
    output: Path,
    *,
    compress_level: int = DEBUG_PNG_COMPRESS_LEVEL,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(f".{output.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        image.save(tmp, format="PNG", compress_level=compress_level)
        os.replace(tmp, output)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def save_bmp_atomic(image: Image.Image, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(f".{output.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        image.save(tmp, format="BMP")
        os.replace(tmp, output)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def capture_filename_for_mode(performance_mode: str) -> str:
    if performance_mode == OCR_PERFORMANCE_HIGH:
        return "screen.bmp"
    return "screen.png"


def capture_png_compress_level_for_mode(performance_mode: str) -> int:
    if performance_mode == OCR_PERFORMANCE_HIGH:
        return PERFORMANCE_PNG_COMPRESS_LEVEL
    return DEBUG_PNG_COMPRESS_LEVEL


def save_capture_atomic(
    image: Image.Image,
    output: Path,
    performance_mode: str,
) -> None:
    if output.suffix.lower() == ".bmp":
        save_bmp_atomic(image, output)
        return
    save_png_atomic(
        image,
        output,
        compress_level=capture_png_compress_level_for_mode(performance_mode),
    )


def load_presets() -> Dict[str, AugmentPreset]:
    raw = load_json(presets_path())
    presets: Dict[str, AugmentPreset] = {}
    for item in raw:
        presets[item["key"]] = AugmentPreset(
            key=item["key"],
            width=int(item["width"]),
            height=int(item["height"]),
            scale=int(item["scale"]),
            anchor_rect=Rect(**item["anchor_rect"]),
            augment_rects=[Rect(**rect) for rect in item["augment_rects"]],
        )
    return presets


def get_preset(key: str) -> AugmentPreset:
    presets = load_presets()
    if key not in presets:
        raise KeyError(f"unknown preset: {key}")
    return presets[key]


def _line_is_black_bar(image: Image.Image, *, axis: str, index: int) -> bool:
    if axis == "row":
        line = image.crop((0, index, image.width, index + 1))
    else:
        line = image.crop((index, 0, index + 1, image.height))
    histogram = line.histogram()
    total = max(sum(histogram), 1)
    black_fraction = sum(histogram[:16]) / total
    mean_luma = sum(value * count for value, count in enumerate(histogram)) / total
    return black_fraction >= 0.97 and mean_luma <= 12.0


def _detect_content_viewport_from_image(raw_image: Image.Image) -> Rect:
    width, height = raw_image.size
    if width <= 0 or height <= 0:
        return Rect(x=0, y=0, width=max(width, 1), height=max(height, 1))

    sample_width = min(width, 640)
    sample_height = max(1, int(round(height * sample_width / width)))
    if sample_height > 360:
        sample_height = 360
        sample_width = max(1, int(round(width * sample_height / height)))
    grayscale = ImageOps.grayscale(raw_image)
    sample = grayscale.resize(
        (sample_width, sample_height),
        Image.Resampling.BOX,
    )

    def leading_margin(axis: str, length: int) -> int:
        margin = 0
        while margin < length and _line_is_black_bar(sample, axis=axis, index=margin):
            margin += 1
        return margin

    def trailing_margin(axis: str, length: int) -> int:
        margin = 0
        while margin < length and _line_is_black_bar(
            sample,
            axis=axis,
            index=length - margin - 1,
        ):
            margin += 1
        return margin

    left_sample = leading_margin("column", sample_width)
    right_sample = trailing_margin("column", sample_width)
    top_sample = leading_margin("row", sample_height)
    bottom_sample = trailing_margin("row", sample_height)

    def refine_margin(
        axis: str,
        approximate: int,
        *,
        leading: bool,
        length: int,
        sample_length: int,
    ) -> int:
        radius = max(3, int(round(length / sample_length)) + 2)
        start = max(0, approximate - radius)
        end = min(length, approximate + radius + 1)
        if leading:
            for index in range(start, end):
                if not _line_is_black_bar(grayscale, axis=axis, index=index):
                    return index
        else:
            for margin in range(start, end):
                index = length - margin - 1
                if index < 0 or not _line_is_black_bar(grayscale, axis=axis, index=index):
                    return margin
        return approximate

    left = refine_margin(
        "column",
        int(round(left_sample * width / sample_width)),
        leading=True,
        length=width,
        sample_length=sample_width,
    )
    right = refine_margin(
        "column",
        int(round(right_sample * width / sample_width)),
        leading=False,
        length=width,
        sample_length=sample_width,
    )
    top = refine_margin(
        "row",
        int(round(top_sample * height / sample_height)),
        leading=True,
        length=height,
        sample_length=sample_height,
    )
    bottom = refine_margin(
        "row",
        int(round(bottom_sample * height / sample_height)),
        leading=False,
        length=height,
        sample_length=sample_height,
    )
    content_width = width - left - right
    content_height = height - top - bottom

    has_meaningful_border = max(left, right) >= max(4, round(width * 0.002)) or max(
        top, bottom
    ) >= max(4, round(height * 0.002))
    content_is_plausible = (
        content_width >= 640
        and content_height >= 360
        and content_width >= width * 0.70
        and content_height >= height * 0.70
    )
    if not has_meaningful_border or not content_is_plausible:
        return Rect(x=0, y=0, width=width, height=height)

    return Rect(x=left, y=top, width=content_width, height=content_height)


def detect_content_viewport(image_path: Path) -> Rect:
    with Image.open(image_path) as image:
        image.load()
        return _detect_content_viewport_from_image(image)


def _centered_lol_ui_viewport(viewport: Rect) -> Rect:
    if viewport.height <= 0:
        return viewport
    if viewport.width / float(viewport.height) < ULTRAWIDE_UI_MIN_ASPECT_RATIO:
        return viewport

    ui_width = int(round(viewport.height * LOL_UI_ASPECT_WIDTH / LOL_UI_ASPECT_HEIGHT))
    if ui_width >= viewport.width:
        return viewport
    return Rect(
        x=viewport.x + (viewport.width - ui_width) // 2,
        y=viewport.y,
        width=ui_width,
        height=viewport.height,
    )


def resolve_preset_for_image(image_path: Path, preferred_key: str) -> AugmentPreset:
    with Image.open(image_path) as image:
        image.load()
        return resolve_preset_for_loaded_image(image, preferred_key)


def resolve_preset_for_loaded_image(
    image: Image.Image,
    preferred_key: str,
) -> AugmentPreset:
    presets = load_presets()
    preferred = presets.get(preferred_key)
    width, height = image.size
    viewport = _detect_content_viewport_from_image(image)
    viewport = _centered_lol_ui_viewport(viewport)
    content_width = viewport.width
    content_height = viewport.height

    if (
        preferred
        and preferred.width == content_width
        and preferred.height == content_height
        and viewport == Rect(x=0, y=0, width=width, height=height)
    ):
        return preferred

    exact_candidates = [
        preset
        for preset in presets.values()
        if preset.width == content_width and preset.height == content_height
    ]
    exact = min(
        exact_candidates,
        key=lambda preset: (
            0 if preferred is not None and preset.scale == preferred.scale else 1,
            abs(preset.scale - preferred.scale) if preferred is not None else preset.scale,
        ),
        default=None,
    )
    if exact is not None:
        return exact.scaled_to_viewport(width, height, viewport)

    actual_ratio = content_width / float(content_height if content_height else 1)
    actual_area = max(1, content_width * content_height)

    def preset_score(preset: AugmentPreset) -> tuple[float, float, int]:
        preset_ratio = preset.width / float(preset.height)
        area_delta = abs((preset.width * preset.height) - actual_area) / float(actual_area)
        preferred_bonus = 0 if preset.key == preferred_key else 1
        return (abs(preset_ratio - actual_ratio), area_delta, preferred_bonus)

    closest = min(presets.values(), key=preset_score, default=None)
    if closest is not None:
        return closest.scaled_to_viewport(width, height, viewport)

    if preferred is not None:
        return preferred.scaled_to_viewport(width, height, viewport)

    raise KeyError(f"unknown preset: {preferred_key}")


def list_presets() -> List[Dict[str, Any]]:
    return [preset.to_dict() for preset in load_presets().values()]


def ensure_output_dir(path: Optional[Path]) -> Path:
    if path is None:
        return Path(tempfile.mkdtemp(prefix="aram-assistant-augment-"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_windows_dpi_awareness() -> None:
    global _DPI_AWARENESS_SET
    if _DPI_AWARENESS_SET or sys.platform != "win32":
        return

    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
    _DPI_AWARENESS_SET = True


LOL_WINDOW_TITLE_TOKENS = [
    "league of legends (tm) client",
    "league of legends(tm) client",
]

ASSISTANT_WINDOW_TITLE_TOKENS = [
    "海斗小帮手",
    "海克斯盒子",
    "海克斯大乱斗助手",
    "aramassistant",
    "aram assistant",
    "hexgod",
]

OVERLAY_WINDOW_TITLE_TOKENS = [
    "hexgod · 实时建议",
    "aram augment overlay",
    "aram champ select overlay",
    "aram item overlay",
    "augment-overlay",
    "champ-select-overlay",
    "formula-item-overlay",
    "hexgod · 强化悬浮窗",
    "hexgod · 选人数据",
    "hexgod · 精选装备",
    "强化悬浮窗",
    "选人数据",
    "精选装备",
    "league akari",
]


def title_contains_any(title: str, tokens: Sequence[str]) -> bool:
    lowered = (title or "").strip().lower()
    return bool(lowered and any(token.lower() in lowered for token in tokens))


def classify_foreground_title(title: str) -> Dict[str, bool]:
    return {
        "lol": title_contains_any(title, LOL_WINDOW_TITLE_TOKENS),
        "assistant": title_contains_any(title, ASSISTANT_WINDOW_TITLE_TOKENS),
        "overlay": title_contains_any(title, OVERLAY_WINDOW_TITLE_TOKENS),
    }


def find_lol_window_context() -> Dict[str, Any]:
    context: Dict[str, Any] = {
        "bbox": None,
        "hwnd": None,
        "window_rect": None,
        "client_rect": None,
        "dpi": None,
        "dpi_scale": None,
        "monitor_number": None,
        "monitor_device": None,
        "monitor_rect": None,
        "monitor_work_rect": None,
        "monitor_primary": None,
        "window_mode": None,
        "foreground_title": None,
        "foreground_matches_lol": False,
        "foreground_matches_assistant": False,
        "foreground_matches_overlay": False,
        "lol_window_title": None,
        "lol_window_foreground": False,
    }
    if sys.platform != "win32":
        return context

    ensure_windows_dpi_awareness()
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    foreground = user32.GetForegroundWindow()

    def title_for(hwnd: int) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value.strip()

    def client_rect_for(hwnd: int) -> Optional[tuple[int, int, int, int]]:
        rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        top_left = wintypes.POINT(rect.left, rect.top)
        bottom_right = wintypes.POINT(rect.right, rect.bottom)
        if not user32.ClientToScreen(hwnd, ctypes.byref(top_left)):
            return None
        if not user32.ClientToScreen(hwnd, ctypes.byref(bottom_right)):
            return None
        width = bottom_right.x - top_left.x
        height = bottom_right.y - top_left.y
        if user32.IsIconic(hwnd) or width < 640 or height < 360:
            return None
        return (top_left.x, top_left.y, bottom_right.x, bottom_right.y)

    def window_metadata_for(
        hwnd: int,
        client_bbox: tuple[int, int, int, int],
    ) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {
            "hwnd": int(hwnd),
            "client_rect": list(client_bbox),
        }
        window_rect = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
            metadata["window_rect"] = [
                window_rect.left,
                window_rect.top,
                window_rect.right,
                window_rect.bottom,
            ]

        dpi = 96
        try:
            get_dpi_for_window = user32.GetDpiForWindow
            get_dpi_for_window.argtypes = [wintypes.HWND]
            get_dpi_for_window.restype = wintypes.UINT
            detected_dpi = int(get_dpi_for_window(hwnd))
            if detected_dpi > 0:
                dpi = detected_dpi
        except Exception:
            pass
        metadata["dpi"] = dpi
        metadata["dpi_scale"] = round(dpi / 96.0, 4)

        class MonitorInfoEx(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32),
            ]

        try:
            monitor = user32.MonitorFromWindow(hwnd, 2)
            info = MonitorInfoEx()
            info.cbSize = ctypes.sizeof(MonitorInfoEx)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                monitor_rect = [
                    info.rcMonitor.left,
                    info.rcMonitor.top,
                    info.rcMonitor.right,
                    info.rcMonitor.bottom,
                ]
                device = str(info.szDevice)
                number_match = re.search(r"(\d+)$", device)
                metadata.update(
                    {
                        "monitor_number": (
                            int(number_match.group(1)) if number_match else None
                        ),
                        "monitor_device": device or None,
                        "monitor_rect": monitor_rect,
                        "monitor_work_rect": [
                            info.rcWork.left,
                            info.rcWork.top,
                            info.rcWork.right,
                            info.rcWork.bottom,
                        ],
                        "monitor_primary": bool(info.dwFlags & 1),
                    }
                )
                client_left, client_top, client_right, client_bottom = client_bbox
                monitor_left, monitor_top, monitor_right, monitor_bottom = monitor_rect
                fills_monitor = all(
                    abs(actual - expected) <= 2
                    for actual, expected in zip(
                        (client_left, client_top, client_right, client_bottom),
                        (monitor_left, monitor_top, monitor_right, monitor_bottom),
                    )
                )
                metadata["window_mode"] = (
                    "borderless_or_fullscreen" if fills_monitor else "windowed"
                )
        except Exception:
            pass
        return metadata

    def matches_lol(hwnd: int) -> bool:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = kernel32.OpenProcess(0x1000, False, pid.value)
        if process:
            try:
                path = ctypes.create_unicode_buffer(2048)
                size = wintypes.DWORD(len(path))
                if kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
                    return Path(path.value).name.casefold() == 'league of legends.exe'
            finally:
                kernel32.CloseHandle(process)
        # Querying an elevated process may fail. Its native game class/title
        # still separate it from the Chromium lobby (LeagueClientUx.exe).
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, len(name))
        return name.value == 'RiotWindowClass' or title_for(hwnd).casefold() in LOL_WINDOW_TITLE_TOKENS

    foreground_title = title_for(foreground) if foreground else ""
    foreground_classification = classify_foreground_title(foreground_title)
    context["foreground_title"] = foreground_title or None
    context["foreground_matches_lol"] = bool(foreground and matches_lol(foreground))
    context["foreground_matches_assistant"] = foreground_classification["assistant"]
    context["foreground_matches_overlay"] = foreground_classification["overlay"]
    if context["foreground_matches_lol"]:
        bbox = client_rect_for(foreground)
        if bbox is not None:
            context.update(
                {
                    "bbox": bbox,
                    "lol_window_title": foreground_title,
                    "lol_window_foreground": True,
                    **window_metadata_for(foreground, bbox),
                }
            )
            return context

    matches: List[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_windows(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if matches_lol(hwnd):
            matches.append(int(hwnd))
        return True

    user32.EnumWindows(enum_windows, 0)
    for hwnd in matches:
        bbox = client_rect_for(hwnd)
        if bbox is not None:
            context.update(
                {
                    "bbox": bbox,
                    "lol_window_title": title_for(hwnd) or None,
                    "lol_window_foreground": hwnd == foreground,
                    **window_metadata_for(hwnd, bbox),
                }
            )
            return context
    return context


def foreground_allows_live_capture(
    window_context: Dict[str, Any],
    *,
    allow_non_lol_foreground: bool = False,
) -> bool:
    if allow_non_lol_foreground:
        return True
    return bool(
        window_context.get("foreground_matches_lol")
        or window_context.get("foreground_matches_overlay")
    )


def foreground_blocks_live_capture(
    window_context: Dict[str, Any],
    *,
    allow_non_lol_foreground: bool = False,
) -> Optional[str]:
    if foreground_allows_live_capture(
        window_context,
        allow_non_lol_foreground=allow_non_lol_foreground,
    ):
        return None
    if window_context.get("foreground_matches_assistant"):
        return "assistant_window_foreground"
    return "lol_window_not_foreground"


def find_lol_window_bbox() -> Optional[tuple[int, int, int, int]]:
    return find_lol_window_context().get("bbox")


class CaptureSkipped(RuntimeError):
    def __init__(self, reason: str, target: Path, meta: Dict[str, Any]):
        super().__init__(reason)
        self.target = target
        self.meta = meta


def capture_screen(
    target: Optional[Path] = None,
    *,
    allow_non_lol_foreground: bool = False,
    performance_mode: str = OCR_PERFORMANCE_BALANCED,
) -> Path:
    output = target or (
        Path(tempfile.mkdtemp(prefix="aram-screen-"))
        / capture_filename_for_mode(performance_mode)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    ensure_windows_dpi_awareness()
    window_context = find_lol_window_context()
    bbox = window_context.get("bbox")
    foreground_kind = (
        "lol"
        if window_context.get("foreground_matches_lol")
        else "overlay"
        if window_context.get("foreground_matches_overlay")
        else "assistant"
        if window_context.get("foreground_matches_assistant")
        else "other"
        if window_context.get("foreground_title")
        else "none"
    )
    capture_meta: Dict[str, Any] = {
        "target": str(output),
        "timestamp_ms": int(time.time() * 1000),
        "lol_window_bbox": list(bbox) if bbox is not None else None,
        "lol_window_hwnd": window_context.get("hwnd"),
        "lol_window_rect": window_context.get("window_rect"),
        "lol_client_rect": window_context.get("client_rect"),
        "lol_window_mode": window_context.get("window_mode"),
        "dpi": window_context.get("dpi"),
        "dpi_scale": window_context.get("dpi_scale"),
        "monitor_number": window_context.get("monitor_number"),
        "monitor_device": window_context.get("monitor_device"),
        "monitor_rect": window_context.get("monitor_rect"),
        "monitor_work_rect": window_context.get("monitor_work_rect"),
        "monitor_primary": window_context.get("monitor_primary"),
        "foreground_kind": foreground_kind,
        "foreground_matches_lol": window_context.get("foreground_matches_lol"),
        "foreground_matches_assistant": window_context.get("foreground_matches_assistant"),
        "foreground_matches_overlay": window_context.get("foreground_matches_overlay"),
        "lol_window_foreground": window_context.get("lol_window_foreground"),
        "allow_non_lol_foreground_requested": allow_non_lol_foreground,
        "method": None,
        "image_size": None,
        "content_viewport": None,
    }

    if sys.platform == "win32" and bbox is None:
        capture_meta["error"] = "lol_window_not_found"
        write_json(output.parent / "screen_meta.json", capture_meta)
        raise RuntimeError("未找到 LoL 游戏窗口，已停止本次识别截图。")

    if sys.platform == "win32" and bbox is not None:
        blocking_reason = foreground_blocks_live_capture(
            window_context,
            allow_non_lol_foreground=allow_non_lol_foreground,
        )
    else:
        blocking_reason = None

    if sys.platform == "win32" and bbox is not None and blocking_reason is not None:
        capture_meta["error"] = blocking_reason
        write_json(output.parent / "screen_meta.json", capture_meta)
        if blocking_reason == "assistant_window_foreground":
            raise CaptureSkipped(
                "assistant_window_foreground: 助手主窗口在前台，已跳过本次识别，避免把桌面误识别为游戏。",
                output,
                capture_meta,
            )
        raise CaptureSkipped(
            "lol_window_not_foreground: LoL 游戏窗口不在前台，已跳过本次识别。",
            output,
            capture_meta,
        )

    try:
        import mss
        with mss.mss() as sct:
            if bbox is not None:
                left, top, right, bottom = bbox
                monitor = {
                    "left": left,
                    "top": top,
                    "width": right - left,
                    "height": bottom - top,
                }
            else:
                monitor = sct.monitors[1]
            img_data = sct.grab(monitor)
            image = Image.frombytes("RGB", (img_data.width, img_data.height), img_data.rgb)
            try:
                content_viewport = _detect_content_viewport_from_image(image).to_dict()
                save_capture_atomic(image, output, performance_mode)
            finally:
                image.close()
            capture_meta.update(
                {
                    "method": "mss",
                    "monitor": dict(monitor),
                    "image_size": [img_data.width, img_data.height],
                    "content_viewport": content_viewport,
                }
            )
            write_json(output.parent / "screen_meta.json", capture_meta)
            return output
    except Exception:
        pass

    try:
        if bbox is not None:
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
        else:
            image = ImageGrab.grab(all_screens=False)
        try:
            content_viewport = _detect_content_viewport_from_image(image).to_dict()
            save_capture_atomic(image, output, performance_mode)
        finally:
            image.close()
        with Image.open(output) as image:
            capture_meta.update(
                {
                    "method": "ImageGrab",
                    "image_size": list(image.size),
                    "content_viewport": content_viewport,
                }
            )
        write_json(output.parent / "screen_meta.json", capture_meta)
    except OSError:
        # Fallback to mss when PIL ImageGrab fails (e.g. remote desktop, DPI issues)
        import mss
        with mss.mss() as sct:
            if bbox is not None:
                left, top, right, bottom = bbox
                monitor = {
                    "left": left,
                    "top": top,
                    "width": right - left,
                    "height": bottom - top,
                }
            else:
                monitor = sct.monitors[1]  # Primary monitor
            img_data = sct.grab(monitor)
            # Convert to PIL Image and save
            pil_img = Image.frombytes("RGB", (img_data.width, img_data.height),
                                       img_data.rgb)
            try:
                content_viewport = _detect_content_viewport_from_image(pil_img).to_dict()
                save_capture_atomic(pil_img, output, performance_mode)
            finally:
                pil_img.close()
            capture_meta.update(
                {
                    "method": "mss-fallback",
                    "monitor": dict(monitor),
                    "image_size": [img_data.width, img_data.height],
                    "content_viewport": content_viewport,
                }
            )
            write_json(output.parent / "screen_meta.json", capture_meta)
    return output


def crop_rect(
    image_path: Path,
    rect: Rect,
    output_path: Path,
    *,
    compress_level: int = DEBUG_PNG_COMPRESS_LEVEL,
) -> Path:
    with Image.open(image_path) as image:
        image.load()
        return crop_rect_from_image(
            image,
            rect,
            output_path,
            compress_level=compress_level,
        )


def crop_rect_from_image(
    image: Image.Image,
    rect: Rect,
    output_path: Path,
    *,
    compress_level: int = DEBUG_PNG_COMPRESS_LEVEL,
) -> Path:
    left, top, right, bottom = rect.to_bbox()
    width, height = image.size
    left = max(0, min(left, width))
    top = max(0, min(top, height))
    right = max(left + 1, min(right, width))
    bottom = max(top + 1, min(bottom, height))
    crop = image.crop((left, top, right, bottom))
    try:
        save_png_atomic(crop, output_path, compress_level=compress_level)
    finally:
        crop.close()
    return output_path


def centered_title_focus_rect(rect: Rect) -> Optional[Rect]:
    if rect.width < 400 or rect.height < 100:
        return None
    width = max(1, int(rect.width * 0.54))
    height = max(1, int(rect.height * 0.71))
    return Rect(
        x=rect.x + (rect.width - width) // 2,
        y=rect.y - int(rect.height / 6),
        width=width,
        height=height,
    )


def variant_rects_for_slot(slot_index: int, rect: Rect) -> List[Rect]:
    focused_title = centered_title_focus_rect(rect)
    base_variants = ([focused_title] if focused_title is not None else []) + [
        rect.shifted_expanded(0, -30, 0, 20),
        rect.shifted_expanded(0, -36, 0, 0),
        rect.shifted_expanded(0, 36, 0, 0),
        rect.shifted_expanded(-80, 0, 160, 0),
        rect.shifted_expanded(-120, -24, 240, 24),
        rect.shifted_expanded(-160, -48, 320, 48),
        rect.shifted_expanded(-60, -15, 120, 0),
        rect.shifted_expanded(-30, -15, 60, -20),
        rect.shifted_expanded(0, 15, 0, -20),
        rect.shifted_expanded(-60, 0, 120, 20),
        rect.shifted_expanded(0, -30, 0, 20),
        rect.shifted_expanded(-80, -15, 160, 20),
        rect.shifted_expanded(-20, 30, 40, -10),
    ]
    variants = base_variants
    if slot_index in (2, 3):
        # Center/right cards are most sensitive to LoL window/capture drift, but
        # the recognizer still expects a single title line, not a tall card crop.
        priority_variants = ([focused_title] if focused_title is not None else []) + [
            rect.shifted_expanded(0, -30, 0, 20),
            rect.shifted_expanded(0, -36, 0, 0),
            rect.shifted_expanded(-60, 0, 120, 20),
            rect.shifted_expanded(-80, 0, 160, 0),
        ]
        extended_variants = [
            rect.shifted_expanded(-100, -15, 200, 0),
            rect.shifted_expanded(-100, 0, 200, 20),
            rect.shifted_expanded(-120, -30, 240, 20),
        ]
        variants = []
        for candidate in [*priority_variants, *base_variants, *extended_variants]:
            if candidate not in variants:
                variants.append(candidate)
    return variants


def primary_rect_for_slot(slot_index: int, rect: Rect) -> Rect:
    return rect.shifted_expanded(0, -30, 0, 20)


CAPTURE_CONTEXT_KEYS = (
    "timestamp_ms",
    "lol_window_bbox",
    "lol_window_hwnd",
    "lol_window_rect",
    "lol_client_rect",
    "lol_window_mode",
    "dpi",
    "dpi_scale",
    "monitor_number",
    "monitor_device",
    "monitor_rect",
    "monitor_work_rect",
    "monitor_primary",
    "foreground_kind",
    "foreground_matches_lol",
    "foreground_matches_assistant",
    "foreground_matches_overlay",
    "lol_window_foreground",
    "method",
    "image_size",
    "content_viewport",
    "error",
)


def capture_context_for_image(image_path: Path) -> Dict[str, Any]:
    meta_path = image_path.parent / "screen_meta.json"
    if not meta_path.exists():
        return {}
    try:
        raw = load_json(meta_path)
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {key: raw.get(key) for key in CAPTURE_CONTEXT_KEYS if key in raw}


def crop_preset(
    image_path: Path,
    preset: AugmentPreset,
    output_dir: Optional[Path] = None,
    *,
    variant_limit: Optional[int] = None,
    source_image: Optional[Image.Image] = None,
    crop_compress_level: int = DEBUG_PNG_COMPRESS_LEVEL,
) -> Dict[str, Any]:
    output = ensure_output_dir(output_dir)
    if source_image is not None:
        return _crop_preset_from_loaded_image(
            image_path,
            preset,
            output,
            source_image,
            variant_limit=variant_limit,
            crop_compress_level=crop_compress_level,
        )
    with Image.open(image_path) as image:
        image.load()
        return _crop_preset_from_loaded_image(
            image_path,
            preset,
            output,
            image,
            variant_limit=variant_limit,
            crop_compress_level=crop_compress_level,
        )


def _crop_preset_from_loaded_image(
    image_path: Path,
    preset: AugmentPreset,
    output: Path,
    image: Image.Image,
    *,
    variant_limit: Optional[int],
    crop_compress_level: int,
) -> Dict[str, Any]:
    image_size = list(image.size)
    anchor_path = crop_rect_from_image(
        image,
        preset.anchor_rect,
        output / "anchor.png",
        compress_level=crop_compress_level,
    )
    augment_paths: List[str] = []
    augment_rects: List[Dict[str, int]] = []
    augment_variant_paths: List[List[str]] = []
    augment_variant_rects: List[List[Dict[str, int]]] = []
    for index, rect in enumerate(preset.augment_rects, start=1):
        primary_rect = primary_rect_for_slot(index, rect)
        crop_path = crop_rect_from_image(
            image,
            primary_rect,
            output / f"augment_{index}.png",
            compress_level=crop_compress_level,
        )
        augment_paths.append(str(crop_path))
        augment_rects.append(primary_rect.to_dict())
        variants = [str(crop_path)]
        variant_rects = [primary_rect.to_dict()]
        # Nearby title crops absorb small DPI and client-window drift.
        variant_rects_all = [
            candidate
            for candidate in variant_rects_for_slot(index, rect)
            if candidate != primary_rect
        ]
        if variant_limit is None:
            crop_variant_rects = variant_rects_all
        else:
            crop_variant_rects = variant_rects_all[: max(0, variant_limit)]
        for variant_index, variant_rect in enumerate(variant_rects_all, start=1):
            variant_path = output / f"augment_{index}_variant_{variant_index}.png"
            if variant_rect in crop_variant_rects:
                crop_rect_from_image(
                    image,
                    variant_rect,
                    variant_path,
                    compress_level=crop_compress_level,
                )
            variants.append(str(variant_path))
            variant_rects.append(variant_rect.to_dict())
        augment_variant_paths.append(variants)
        augment_variant_rects.append(variant_rects)
    capture_context = capture_context_for_image(image_path)
    content_viewport = preset.content_viewport or Rect(
        x=0,
        y=0,
        width=image_size[0],
        height=image_size[1],
    )
    crop_meta = {
        "output_dir": str(output),
        "source_image": str(image_path),
        "image_size": image_size,
        "content_viewport": content_viewport.to_dict(),
        "preset": preset.to_dict(),
        "capture_context": capture_context,
        "lol_window_bbox": capture_context.get("lol_window_bbox"),
        "anchor_path": str(anchor_path),
        "anchor_rect": preset.anchor_rect.to_dict(),
        "augment_paths": augment_paths,
        "augment_rects": augment_rects,
        "augment_variant_paths": augment_variant_paths,
        "augment_variant_rects": augment_variant_rects,
    }
    crop_meta_path = output / "ocr-crop-info.json"
    write_json(crop_meta_path, crop_meta)
    return {
        "output_dir": str(output),
        "image_size": image_size,
        "content_viewport": content_viewport.to_dict(),
        "capture_context": capture_context,
        "anchor_path": str(anchor_path),
        "anchor_rect": preset.anchor_rect.to_dict(),
        "augment_paths": augment_paths,
        "augment_rects": augment_rects,
        "augment_variant_paths": augment_variant_paths,
        "augment_variant_rects": augment_variant_rects,
        "crop_meta_path": str(crop_meta_path),
    }


def detect_anchor(anchor_path: Path) -> AnchorDetection:
    with Image.open(anchor_path) as raw_image:
        image = raw_image.convert("RGB")
    grayscale = ImageOps.grayscale(image)
    stats = ImageStat.Stat(grayscale)
    mean_luma = float(stats.mean[0]) if stats.mean else 0.0
    stdev_luma = float(stats.stddev[0]) if stats.stddev else 0.0

    rgb = image.load()
    width, height = image.size
    total = max(width * height, 1)
    bright_pixels = 0
    warm_pixels = 0
    for y in range(height):
        for x in range(width):
            r, g, b = rgb[x, y]
            if r > 180 and g > 145 and b < 180:
                warm_pixels += 1
            if r > 145 and g > 145 and b > 145:
                bright_pixels += 1

    bright_ratio = bright_pixels / total
    warm_ratio = warm_pixels / total
    has_warm_card_ui = warm_ratio >= 0.015 and bright_ratio >= 0.02 and (
        (mean_luma >= 95.0 and stdev_luma >= 18.0)
        or stdev_luma >= 45.0
    )
    has_prismatic_card_ui = (
        45.0 <= mean_luma <= 135.0
        and stdev_luma >= 45.0
        and 0.06 <= bright_ratio <= 0.45
    )
    has_silver_card_ui = (
        25.0 <= mean_luma <= 65.0
        and stdev_luma >= 40.0
        and 0.005 <= bright_ratio <= 0.05
    )
    detected = (
        has_warm_card_ui
        or has_prismatic_card_ui
        or has_silver_card_ui
        or (warm_ratio >= 0.10 and mean_luma >= 80.0 and stdev_luma >= 12.0)
    )
    reason = (
        f"mean={mean_luma:.1f}, stdev={stdev_luma:.1f}, "
        f"bright_ratio={bright_ratio:.3f}, warm_ratio={warm_ratio:.3f}"
    )
    return AnchorDetection(
        detected=detected,
        mean_luma=mean_luma,
        stdev_luma=stdev_luma,
        bright_ratio=bright_ratio,
        warm_ratio=warm_ratio,
        reason=reason,
    )




def load_runtime(data_dir: Optional[Path] = None) -> Dict[str, Any]:
    root = data_dir or resources_data_dir()
    return {
        "meta": load_json(root / "runtime_meta.json"),
        "augments": load_json(root / "augments.json"),
        "champions": load_json(root / "champions.json"),
        "augment_aliases": load_json(root / "augment_aliases.json"),
        "hero_augment_stats": load_json(root / "hero_augment_stats.json"),
        "hero_item_builds": load_json(root / "hero_item_builds.json"),
        "hero_augment_trios": load_json(root / "hero_augment_trios.json"),
    }


def match_ocr_texts(
    ocr_texts: Sequence[str],
    alias_map: Dict[str, List[str]],
    threshold: float = 90.0,
) -> List[Dict[str, Any]]:
    # Normalize keys in alias_map to prevent punctuation mismatch issues (e.g. "升级：中娅" vs "升级:中娅")
    norm_alias_map = {}
    for key, val in alias_map.items():
        norm_alias_map[normalize_text(key)] = val

    normalized_keys = list(norm_alias_map.keys())
    matches: List[Dict[str, Any]] = []

    for text in ocr_texts:
        normalized = normalize_text(text)
        if not normalized:
            matches.append(
                {
                    "text": text,
                    "normalized_text": normalized,
                    "matched_name": None,
                    "score": 0.0,
                    "mode": "unmatched",
                }
            )
            continue

        if normalized in norm_alias_map:
            matches.append(
                {
                    "text": text,
                    "normalized_text": normalized,
                    "matched_name": norm_alias_map[normalized][0],
                    "score": 100.0,
                    "mode": "exact",
                }
            )
            continue

        # A one-character OCR fragment has no useful edit-distance signal and
        # otherwise creates high-scoring false matches during ordinary combat.
        if len(normalized) < 2:
            matches.append(
                {
                    "text": text,
                    "normalized_text": normalized,
                    "matched_name": None,
                    "score": 0.0,
                    "mode": "unmatched",
                }
            )
            continue

        exactish = exactish_match(normalized, normalized_keys)
        if exactish:
            matches.append(
                {
                    "text": text,
                    "normalized_text": normalized,
                    "matched_name": norm_alias_map[exactish][0],
                    "score": 98.0,
                    "mode": "fuzzy",
                }
            )
            continue

        fuzzy = best_fuzzy_match(normalized, normalized_keys)
        if fuzzy and float(fuzzy["score"]) >= threshold:
            key = fuzzy["key"]
            score = fuzzy["score"]
            matches.append(
                {
                    "text": text,
                    "normalized_text": normalized,
                    "matched_name": norm_alias_map[key][0],
                    "score": float(score),
                    "mode": "fuzzy",
                }
            )
            continue

        matches.append(
            {
                "text": text,
                "normalized_text": normalized,
                "matched_name": None,
                "score": float(fuzzy["score"]) if fuzzy else 0.0,
                "mode": "unmatched",
            }
        )

    return matches


def exactish_match(text: str, candidates: Sequence[str]) -> Optional[str]:
    text_len = len(text)
    if text_len < 2:
        return None
    for candidate in candidates:
        if len(candidate) < 2:
            continue
        if text_len <= 2 and len(candidate) != text_len:
            continue
        if abs(text_len - len(candidate)) > 2:
            continue
        if candidate in text or text in candidate:
            return candidate
    return None


def best_fuzzy_match(text: str, candidates: Sequence[str]) -> Optional[Dict[str, Any]]:
    try:
        from rapidfuzz import fuzz, process  # type: ignore

        fuzzy = process.extractOne(text, list(candidates), scorer=fuzz.WRatio)
        if fuzzy:
            key, score, _ = fuzzy
            return {"key": key, "score": float(score)}
        return None
    except Exception:
        best_key: Optional[str] = None
        best_score = 0.0
        for candidate in candidates:
            score = fallback_similarity_score(text, candidate)
            if score > best_score:
                best_key = candidate
                best_score = score
        if best_key is None:
            return None
        return {"key": best_key, "score": best_score}


def fallback_similarity_score(left: str, right: str) -> float:
    if not left and not right:
        return 100.0
    max_len = max(len(left), len(right), 1)
    distance = levenshtein_distance(left, right)
    return max(0.0, (1.0 - distance / max_len) * 100.0)


def levenshtein_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    current = [0] * (len(right) + 1)
    for i, left_char in enumerate(left, start=1):
        current[0] = i
        for j, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            current[j] = min(
                current[j - 1] + 1,
                previous[j] + 1,
                previous[j - 1] + cost,
            )
        previous, current = current, previous
    return previous[-1]


def canonicalize_hero_name(runtime: Dict[str, Any], hero: str) -> str:
    hero = (hero or "").strip()
    if not hero:
        return hero
    champions = runtime.get("champions", [])
    for row in champions:
        if row.get("hero") == hero:
            return row["hero"]
    locke_aliases = {"Locke", "洛克", "灰烬驱魔人"}
    if hero in locke_aliases:
        for row in champions:
            if row.get("hero") in locke_aliases or row.get("full_name") in locke_aliases:
                return row.get("hero") or hero
    for row in champions:
        full_name = (row.get("full_name") or "").strip()
        if full_name == hero or full_name.startswith(hero) or full_name.endswith(hero):
            return row.get("hero") or hero
    return hero


def recommend_items(runtime: Dict[str, Any], hero: str, limit: int = 5) -> List[Dict[str, Any]]:
    hero = canonicalize_hero_name(runtime, hero)
    rows = list(runtime["hero_item_builds"].get(hero, []))
    rows.sort(key=lambda row: (-row["win_rate"], -row["games"]))
    return rows[:limit]


def recommend_augments(
    runtime: Dict[str, Any],
    hero: str,
    matched_augments: Sequence[str],
    limit: int = 3,
) -> List[Dict[str, Any]]:
    hero = canonicalize_hero_name(runtime, hero)
    rows = list(runtime["hero_augment_stats"].get(hero, []))
    matched = [name for name in matched_augments if name]
    if matched:
        rows = [row for row in rows if row["augment_name"] in matched]
    else:
        return []

    rows.sort(key=lambda row: (-row["score"], -row["games"]))
    return rows[:limit]


def run_single_ocr(
    image_path: Path,
    *,
    backend: str = "auto",
    grayscale: bool = True,
    invert: bool = False,
    max_side: int = 1600,
) -> Dict[str, Any]:
    python = os.environ.get("ARAM_ASSISTANT_OCR_PYTHON", sys.executable)
    command = [
        python,
        "-m", "hexgod.vision.recognizer",
        "--image",
        str(image_path),
        "--backend",
        backend,
        "--max-side",
        str(max_side),
    ]
    if grayscale:
        command.append("--grayscale")
    if invert:
        command.append("--invert")
    env = os.environ.copy()
    env.setdefault("ARAM_ASSISTANT_OCR_MODEL_DIR", str(resources_model_dir()))
    startupinfo = None
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    return json.loads(completed.stdout)


def run_ocr_images(
    image_paths: Sequence[Path],
    *,
    backend: str = "auto",
    grayscale: bool = True,
    invert: bool = False,
    max_side: int = 1600,
    performance_mode: str = OCR_PERFORMANCE_BALANCED,
) -> List[Dict[str, Any]]:
    if not image_paths:
        return []

    python = os.environ.get("ARAM_ASSISTANT_OCR_PYTHON", sys.executable)
    command = [
        python,
        "-m", "hexgod.vision.recognizer",
        "--images",
        *[str(path) for path in image_paths],
        "--backend",
        backend,
        "--max-side",
        str(max_side),
        "--performance-mode",
        (
            OCR_PERFORMANCE_HIGH
            if performance_mode == OCR_PERFORMANCE_HIGH
            else OCR_PERFORMANCE_BALANCED
        ),
    ]
    if grayscale:
        command.append("--grayscale")
    if invert:
        command.append("--invert")
    env = os.environ.copy()
    env.setdefault("ARAM_ASSISTANT_OCR_MODEL_DIR", str(resources_model_dir()))
    startupinfo = None
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    decoded = json.loads(completed.stdout)
    if isinstance(decoded, dict) and isinstance(decoded.get("payloads"), list):
        return decoded["payloads"]
    if isinstance(decoded, dict):
        return [decoded]
    return []


def payload_primary_text(payload: Dict[str, Any]) -> str:
    lines = payload.get("lines") or []
    if not lines:
        return ""
    return str(lines[0].get("text") or "")


def payload_confidence(payload: Dict[str, Any]) -> float:
    lines = payload.get("lines") or []
    if not lines:
        return 0.0
    first = lines[0]
    raw = first.get("score", first.get("confidence", 0.0))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value * 100.0 if value <= 1.0 else value


NON_TITLE_TEXT_PATTERNS = [
    "双方军团",
    "开始出兵",
    "点击或按",
    "进行查看",
    "输入/help",
    "购买限制",
    "离开基地",
    "召唤师平台",
    "确认",
    "退出",
    "刷新",
    "离开游戏",
    "离开留下来",
    "留下来",
    "碎片",
]

# Two retry rounds across three unresolved slots keep the rightmost slot from
# being starved when the first crop variant misses the title band.
LIVE_VARIANT_MAX_TOTAL = 6
PERFORMANCE_LIVE_VARIANT_MAX_TOTAL = 12


def live_variant_max_total_for_mode(performance_mode: str) -> int:
    if performance_mode == OCR_PERFORMANCE_HIGH:
        return PERFORMANCE_LIVE_VARIANT_MAX_TOTAL
    return LIVE_VARIANT_MAX_TOTAL


def is_title_like_text(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    if any(normalize_text(pattern) in normalized for pattern in NON_TITLE_TEXT_PATTERNS):
        return False
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", normalized))
    if chinese_chars < 2:
        return False
    # Most Chinese augment titles are short. A few titles contain punctuation,
    # so keep the upper bound loose but penalize obvious descriptions.
    if len(normalized) > 12:
        return False
    if re.search(r"\d+[%％秒金币生命值攻击速度法力]", normalized):
        return False
    return True


def is_definite_non_augment_ui_payload(payload: Dict[str, Any]) -> bool:
    text = payload_primary_text(payload)
    normalized = normalize_text(text)
    if not normalized or payload_confidence(payload) < 55.0:
        return False
    if "碎片" in normalized:
        return True

    digit_count = len(re.findall(r"\d", normalized))
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", normalized))
    if digit_count < 2:
        return False
    if re.fullmatch(r"\d+(?:[/:]\d+)+", normalized):
        return True
    return chinese_count < 2 or digit_count >= chinese_count * 2


def summarize_frame_text_evidence(payloads: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    slots = []
    definite_non_augment_count = 0
    for index, payload in enumerate(payloads, start=1):
        text = payload_primary_text(payload)
        definite_non_augment = is_definite_non_augment_ui_payload(payload)
        if definite_non_augment:
            definite_non_augment_count += 1
        slots.append(
            {
                "slot": index,
                "text": text,
                "confidence": payload_confidence(payload),
                "title_like": is_title_like_text(text),
                "definite_non_augment": definite_non_augment,
            }
        )
    return {
        "definite_non_augment_count": definite_non_augment_count,
        "reject_frame": definite_non_augment_count >= 2,
        "slots": slots,
    }


def payload_quality(payload: Dict[str, Any], alias_map: Optional[Dict[str, List[str]]] = None) -> float:
    text = payload_primary_text(payload)
    normalized = normalize_text(text)
    if not normalized:
        return -100.0
    if re.fullmatch(r"[\?？.。,，!！]+", normalized):
        return -50.0 + payload_confidence(payload)
    confidence = payload_confidence(payload)
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", normalized))
    title_bonus = 35.0 if is_title_like_text(text) else -200.0
    return confidence + title_bonus + min(len(normalized), 8) * 1.5 + chinese_chars * 2.0


def choose_best_payload(
    payloads: List[Dict[str, Any]],
    alias_map: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    if not payloads:
        return {"lines": []}

    return max(payloads, key=lambda payload: payload_quality(payload, None))


def empty_ocr_payload(image_path: Optional[Path] = None, *, status: str = "skipped") -> Dict[str, Any]:
    return {
        "image": str(image_path) if image_path is not None else "",
        "preprocessed_image": None,
        "lines": [],
        "status": status,
        "backend": "anchor-gate",
        "error": None,
    }


def normalize_payload_count(
    payloads: List[Dict[str, Any]],
    expected: int,
    image_paths: Optional[Sequence[Path]] = None,
) -> List[Dict[str, Any]]:
    normalized = payloads[:expected]
    while len(normalized) < expected:
        image_path = image_paths[len(normalized)] if image_paths and len(normalized) < len(image_paths) else None
        normalized.append(empty_ocr_payload(image_path))
    return normalized


def known_augment_match(
    text: str,
    alias_map: Optional[Dict[str, List[str]]],
) -> Optional[Dict[str, Any]]:
    if not alias_map:
        return None
    try:
        matches = match_ocr_texts([text], alias_map, threshold=86.0)
    except Exception:
        return None
    if not matches:
        return None
    first = matches[0]
    return first if first.get("matched_name") else None


def known_augment_match_score(text: str, alias_map: Optional[Dict[str, List[str]]]) -> float:
    match = known_augment_match(text, alias_map)
    return float(match.get("score") or 0.0) if match else 0.0


def payload_candidate(
    slot: int,
    image_path: str,
    payload: Dict[str, Any],
    alias_map: Optional[Dict[str, List[str]]],
    variant_index: int,
) -> Dict[str, Any]:
    text = payload_primary_text(payload)
    candidate_frame_quality: Dict[str, Any] = {}
    try:
        with Image.open(Path(image_path)) as raw_image:
            candidate_frame_quality = frame_quality_for_image(raw_image, slot)
    except Exception as err:
        candidate_frame_quality = {"slot": slot, "error": str(err)}
    image_quality = frame_quality_score(candidate_frame_quality)
    return {
        "slot": slot,
        "variant_index": variant_index,
        "image": image_path,
        "text": text,
        "confidence": payload_confidence(payload),
        "image_quality": image_quality,
        "frame_quality": candidate_frame_quality,
        "title_like": is_title_like_text(text),
        "status": payload.get("status"),
        "backend": payload.get("backend"),
        "error": payload.get("error"),
    }


def is_locked_payload(payload: Dict[str, Any], alias_map: Optional[Dict[str, List[str]]]) -> bool:
    text = payload_primary_text(payload)
    return (
        is_title_like_text(text)
        and payload_confidence(payload) >= 80.0
        and known_augment_match_score(text, alias_map) >= 95.0
    )


def infer_augment_frame_from_payloads(
    payloads: Sequence[Dict[str, Any]],
    alias_map: Optional[Dict[str, List[str]]],
) -> Dict[str, Any]:
    observed_confidences: List[float] = []
    slots: List[Dict[str, Any]] = []
    for index, payload in enumerate(payloads, start=1):
        text = payload_primary_text(payload)
        confidence = payload_confidence(payload)
        observed = confidence >= 75.0 and is_title_like_text(text)
        if observed:
            observed_confidences.append(confidence)
        slots.append(
            {
                "slot": index,
                "text": text,
                "confidence": confidence,
                "observed_title": observed,
            }
        )

    average_confidence = (
        sum(observed_confidences) / len(observed_confidences)
        if observed_confidences
        else 0.0
    )
    detected = len(observed_confidences) >= 2 and average_confidence >= 75.0
    return {
        "detected": detected,
        "matched_count": len(observed_confidences),
        "observed_title_count": len(observed_confidences),
        "resolved_count": 0,
        "average_confidence": average_confidence,
        "slots": slots,
    }


def needs_variant_retry(payload: Dict[str, Any], alias_map: Optional[Dict[str, List[str]]] = None) -> bool:
    text = payload_primary_text(payload)
    normalized = normalize_text(text)
    confidence = payload_confidence(payload)
    if not normalized:
        return True
    if re.fullmatch(r"[\?？.。,，!！]+", normalized):
        return True
    if is_locked_payload(payload, alias_map):
        return False
    if not is_title_like_text(text):
        return True
    match_score = known_augment_match_score(text, alias_map)
    if match_score >= 86.0:
        return confidence < 75.0
    if len(normalized) <= 3:
        return True
    return confidence < 82.0


def is_low_information_payload(payload: Dict[str, Any]) -> bool:
    normalized = normalize_text(payload_primary_text(payload))
    if not normalized:
        return True
    return len(normalized) <= 1 and payload_confidence(payload) < 70.0


DESCRIPTION_HINT_TOKENS = [
    "获得",
    "提供",
    "增加",
    "减少",
    "造成",
    "治疗",
    "护盾",
    "护甲",
    "魔抗",
    "法术",
    "攻击",
    "技能",
    "伤害",
    "生命",
    "金币",
    "冷却",
    "移速",
    "穿透",
]


def is_augment_description_like_text(text: str) -> bool:
    normalized = normalize_text(text)
    if len(normalized) < 8:
        return False
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", normalized))
    if chinese_chars < 4:
        return False
    if any(normalize_text(token) in normalized for token in NON_TITLE_TEXT_PATTERNS):
        return False
    if any(normalize_text(token) in normalized for token in DESCRIPTION_HINT_TOKENS):
        return True
    return bool(re.search(r"\d+[%％]", normalized))


def live_variant_budget_for_payload(
    payload: Dict[str, Any],
    alias_map: Optional[Dict[str, List[str]]] = None,
    *,
    trusted_frame_by_matches: bool = False,
    trusted_frame_by_quality: bool = False,
    trusted_slot_by_quality: bool = False,
) -> int:
    text = payload_primary_text(payload)
    confidence = payload_confidence(payload)
    trusted_frame = trusted_frame_by_matches or trusted_frame_by_quality
    trusted_slot_context = trusted_frame_by_matches or (
        trusted_frame_by_quality and trusted_slot_by_quality
    )
    if is_title_like_text(text) and confidence >= 97.0:
        return 0
    if not trusted_frame or not trusted_slot_context:
        return 0
    if is_low_information_payload(payload):
        return 5
    if not is_title_like_text(text):
        if trusted_slot_by_quality and confidence < 70.0:
            return 5
        if trusted_slot_by_quality and is_augment_description_like_text(text):
            return 5
        return 0
    if confidence < 60.0 and trusted_slot_by_quality:
        return 5
    return 3


def frame_quality_for_image(raw_image: Image.Image, slot: int) -> Dict[str, Any]:
    image = ImageOps.grayscale(raw_image)
    stats = ImageStat.Stat(image)
    mean_luma = float(stats.mean[0]) if stats.mean else 0.0
    stdev_luma = float(stats.stddev[0]) if stats.stddev else 0.0
    pixels = image.load()
    width, height = image.size
    total = max(width * height, 1)
    bright = 0
    very_bright = 0
    dark = 0
    bright_rows = 0
    max_bright_row = 0.0
    bright_by_vertical_third = [0, 0, 0]
    pixels_by_vertical_third = [0, 0, 0]
    for y in range(height):
        row_bright = 0
        vertical_third = min((y * 3) // max(height, 1), 2)
        pixels_by_vertical_third[vertical_third] += width
        for x in range(width):
            value = pixels[x, y]
            if value >= 170:
                bright += 1
                row_bright += 1
                bright_by_vertical_third[vertical_third] += 1
            if value >= 220:
                very_bright += 1
            if value <= 45:
                dark += 1
        row_bright_ratio = row_bright / max(width, 1)
        if row_bright_ratio >= 0.10:
            bright_rows += 1
        max_bright_row = max(max_bright_row, row_bright_ratio)
    bright_ratio = bright / total
    very_bright_ratio = very_bright / total
    dark_ratio = dark / total
    bright_row_coverage = bright_rows / max(height, 1)
    vertical_bright_ratios = [
        bright_count / max(pixel_count, 1)
        for bright_count, pixel_count in zip(
            bright_by_vertical_third,
            pixels_by_vertical_third,
        )
    ]
    top_bright_ratio, middle_bright_ratio, bottom_bright_ratio = vertical_bright_ratios
    scoreboard_row_like = (
        top_bright_ratio >= 0.07
        and 0.12 <= bright_row_coverage <= 0.45
        and top_bright_ratio
        >= max(middle_bright_ratio, bottom_bright_ratio, 0.005) * 2.75
    )
    # Short 1080p titles can occupy less than 0.5% of an otherwise dark crop.
    # Keep the signal local to a narrow horizontal text row to avoid combat HUDs.
    sparse_title_like = (
        stdev_luma >= 12.0
        and 0.0025 <= bright_ratio <= 0.02
        and very_bright_ratio <= 0.02
        and dark_ratio >= 0.80
        and bright_row_coverage <= 0.08
        and max_bright_row >= 0.12
    )
    card_like = (stdev_luma >= 16.0 and bright_ratio >= 0.005) or sparse_title_like
    scene_like = (
        bright_ratio >= 0.18
        or very_bright_ratio >= 0.10
        or (bright_row_coverage >= 0.85 and dark_ratio < 0.06)
    )
    # Real augment title crops are dark UI strips with localized bright text.
    # Combat/map crops can satisfy the broad card_like check, but usually
    # lack a dark panel or are bright across most rows.
    ui_like = (
        card_like
        and dark_ratio >= 0.25
        and bright_ratio <= 0.18
        and very_bright_ratio <= 0.10
        and bright_row_coverage < 0.85
        and max_bright_row <= 0.70
    )
    localized_ui = ui_like and bright_row_coverage <= 0.16
    expanded_title_band = (
        ui_like
        and bright_row_coverage <= 0.45
        and max_bright_row >= 0.18
    )
    title_band_like = (
        localized_ui and max_bright_row >= 0.10
    ) or expanded_title_band
    trusted_title_region = localized_ui or title_band_like
    return {
        "slot": slot,
        "mean_luma": mean_luma,
        "stdev_luma": stdev_luma,
        "bright_ratio": bright_ratio,
        "very_bright_ratio": very_bright_ratio,
        "dark_ratio": dark_ratio,
        "bright_row_coverage": bright_row_coverage,
        "max_bright_row": max_bright_row,
        "top_bright_ratio": top_bright_ratio,
        "middle_bright_ratio": middle_bright_ratio,
        "bottom_bright_ratio": bottom_bright_ratio,
        "scoreboard_row_like": scoreboard_row_like,
        "card_like": card_like,
        "sparse_title_like": sparse_title_like,
        "scene_like": scene_like,
        "ui_like": ui_like,
        "localized_ui": localized_ui,
        "expanded_title_band": expanded_title_band,
        "title_band_like": title_band_like,
        "trusted_title_region": trusted_title_region,
    }


def frame_quality_score(quality: Dict[str, Any]) -> float:
    if not quality or quality.get("error"):
        return 0.0
    score = min(float(quality.get("stdev_luma") or 0.0), 60.0)
    if quality.get("trusted_title_region"):
        score += 100.0
    elif quality.get("title_band_like"):
        score += 80.0
    elif quality.get("ui_like"):
        score += 45.0
    if quality.get("scene_like"):
        score -= 120.0
    if quality.get("scoreboard_row_like"):
        score -= 140.0
    return round(score, 3)


def summarize_frame_quality(
    anchor_detection: AnchorDetection,
    slots: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    card_like_count = 0
    ui_like_count = 0
    localized_ui_count = 0
    title_band_like_count = 0
    trusted_title_region_count = 0
    scene_like_count = 0
    scoreboard_row_like_count = 0
    for slot in slots:
        if slot.get("card_like"):
            card_like_count += 1
        if slot.get("ui_like"):
            ui_like_count += 1
        if slot.get("localized_ui"):
            localized_ui_count += 1
        if slot.get("title_band_like"):
            title_band_like_count += 1
        if slot.get("trusted_title_region"):
            trusted_title_region_count += 1
        if slot.get("scene_like"):
            scene_like_count += 1
        if slot.get("scoreboard_row_like"):
            scoreboard_row_like_count += 1
    anchored_partial = (
        anchor_detection.detected
        and trusted_title_region_count >= 2
        and scene_like_count == 0
        and scoreboard_row_like_count < 2
    )
    anchorless_partial = (
        not anchor_detection.detected
        and title_band_like_count >= 2
        and trusted_title_region_count >= 2
        and card_like_count >= 2
        and scene_like_count == 0
        and scoreboard_row_like_count < 2
    )
    anchorless_complete = (
        not anchor_detection.detected
        and title_band_like_count == len(slots)
        and card_like_count == len(slots)
        and scene_like_count == 0
        and scoreboard_row_like_count < 2
    )
    return {
        "anchor_detected": anchor_detection.detected,
        "card_like_count": card_like_count,
        "ui_like_count": ui_like_count,
        "localized_ui_count": localized_ui_count,
        "title_band_like_count": title_band_like_count,
        "trusted_title_region_count": trusted_title_region_count,
        "scene_like_count": scene_like_count,
        "scoreboard_row_like_count": scoreboard_row_like_count,
        "anchored_partial": anchored_partial,
        "anchorless_partial": anchorless_partial,
        "anchorless_complete": anchorless_complete,
        "probable_augment_frame": anchored_partial or anchorless_partial or anchorless_complete,
        "slots": list(slots),
    }


def frame_quality_for_crops(
    anchor_detection: AnchorDetection,
    crop_paths: Sequence[str],
) -> Dict[str, Any]:
    slots: List[Dict[str, Any]] = []
    for index, raw_path in enumerate(crop_paths, start=1):
        try:
            with Image.open(Path(raw_path)) as raw_image:
                slots.append(frame_quality_for_image(raw_image, index))
        except Exception as err:
            slots.append({"slot": index, "error": str(err), "card_like": False})
    return summarize_frame_quality(anchor_detection, slots)


def run_three_box_ocr(
    image_path: Path,
    preset_key: str,
    *,
    output_dir: Optional[Path] = None,
    backend: str = "auto",
    ocr_runner=None,
    mode: str = "live",
    variant_slots: Optional[Sequence[int]] = None,
    performance_mode: str = OCR_PERFORMANCE_BALANCED,
) -> Dict[str, Any]:
    performance_mode = (
        OCR_PERFORMANCE_HIGH
        if performance_mode == OCR_PERFORMANCE_HIGH
        else OCR_PERFORMANCE_BALANCED
    )
    if performance_mode == OCR_PERFORMANCE_HIGH:
        with Image.open(image_path) as source_image:
            source_image.load()
            return _run_three_box_ocr_core(
                image_path,
                preset_key,
                output_dir=output_dir,
                backend=backend,
                ocr_runner=ocr_runner,
                mode=mode,
                variant_slots=variant_slots,
                performance_mode=performance_mode,
                source_image=source_image,
            )
    return _run_three_box_ocr_core(
        image_path,
        preset_key,
        output_dir=output_dir,
        backend=backend,
        ocr_runner=ocr_runner,
        mode=mode,
        variant_slots=variant_slots,
        performance_mode=performance_mode,
        source_image=None,
    )


def _run_three_box_ocr_core(
    image_path: Path,
    preset_key: str,
    *,
    output_dir: Optional[Path],
    backend: str,
    ocr_runner,
    mode: str,
    variant_slots: Optional[Sequence[int]],
    performance_mode: str,
    source_image: Optional[Image.Image],
) -> Dict[str, Any]:
    started_at = time.perf_counter()
    last_mark = started_at

    def mark(name: str, timings: Dict[str, int]) -> None:
        nonlocal last_mark
        now = time.perf_counter()
        timings[name] = round((now - last_mark) * 1000)
        last_mark = now

    timings: Dict[str, int] = {}
    requested_variant_slots = (
        {
            int(slot)
            for slot in variant_slots
            if isinstance(slot, int) and 1 <= int(slot) <= 3
        }
        if variant_slots is not None
        else None
    )
    preset = (
        resolve_preset_for_loaded_image(source_image, preset_key)
        if source_image is not None
        else resolve_preset_for_image(image_path, preset_key)
    )
    crop_info = crop_preset(
        image_path,
        preset,
        output_dir,
        variant_limit=0 if mode == "live" else None,
        source_image=source_image,
        crop_compress_level=capture_png_compress_level_for_mode(performance_mode),
    )
    mark("crop_ms", timings)
    anchor_detection = detect_anchor(Path(crop_info["anchor_path"]))
    mark("anchor_ms", timings)
    expected_count = len(crop_info["augment_paths"])
    runner = ocr_runner or (
        lambda paths, backend_name: run_ocr_images(
            [Path(path) for path in paths],
            backend=backend_name,
            performance_mode=performance_mode,
        )
    )
    frame_quality = frame_quality_for_crops(anchor_detection, crop_info["augment_paths"])
    augment_paths = [Path(path) for path in crop_info["augment_paths"]]
    if mode == "live" and not frame_quality.get("probable_augment_frame"):
        payloads = [
            empty_ocr_payload(path, status="frame-gate")
            for path in augment_paths[:expected_count]
        ]
        payloads = normalize_payload_count(payloads, expected_count, augment_paths)
        ocr_inference = infer_augment_frame_from_payloads(payloads, None)
        timings["frame_gate_ms"] = round((time.perf_counter() - last_mark) * 1000)
        timings["base_ocr_ms"] = 0
        timings["alias_load_ms"] = 0
        timings["variant_ocr_ms"] = 0
        timings["match_ms"] = 0
        timings["total_ms"] = round((time.perf_counter() - started_at) * 1000)
        return {
            "preset": preset.to_dict(),
            "crop_info": crop_info,
            "anchor_detection": anchor_detection.to_dict(),
            "ocr_inferred_augment_frame": False,
            "ocr_inference": ocr_inference,
            "timings": timings,
            "slot_candidates": [],
            "variant_budget_used": 0,
            "variant_slots_requested": sorted(requested_variant_slots or []),
            "worker_backend": "pipeline",
            "performance_mode": performance_mode,
            "frame_quality": frame_quality,
            "payloads": payloads,
        }
    payloads = normalize_payload_count(runner(crop_info["augment_paths"], backend), expected_count, augment_paths)
    mark("base_ocr_ms", timings)
    alias_map = None
    timings["alias_load_ms"] = 0
    text_evidence = summarize_frame_text_evidence(payloads)
    frame_quality["text_evidence"] = text_evidence
    if text_evidence["reject_frame"]:
        frame_quality["visual_probable_augment_frame"] = bool(
            frame_quality.get("probable_augment_frame")
        )
        frame_quality["probable_augment_frame"] = False
        frame_quality["text_rejection_reason"] = "definite_non_augment_ui"
    base_ocr_inference = infer_augment_frame_from_payloads(payloads, None)
    trusted_frame_by_matches = bool(
        base_ocr_inference.get("matched_count", 0) >= 2
        and not text_evidence["reject_frame"]
    )
    trusted_frame_by_quality = bool(frame_quality.get("probable_augment_frame"))
    frame_quality_slots = frame_quality.get("slots") or []
    variant_groups = crop_info.get("augment_variant_paths") or []
    variant_rect_groups = crop_info.get("augment_variant_rects") or []
    slot_candidates: List[List[Dict[str, Any]]] = []
    for index, payload in enumerate(payloads, start=1):
        image_for_slot = crop_info["augment_paths"][index - 1] if index - 1 < len(crop_info["augment_paths"]) else ""
        slot_candidates.append([payload_candidate(index, image_for_slot, payload, None, 0)])

    variant_budget_used = 0
    desired_retry_paths: List[List[str]] = [[] for _ in payloads]
    lazy_variant_crops: List[tuple[Path, Rect]] = []
    live_variant_max_total = live_variant_max_total_for_mode(performance_mode)
    for index, payload in enumerate(payloads):
        if index >= len(variant_groups):
            continue
        retry_paths = variant_groups[index][1:]
        slot = index + 1
        if requested_variant_slots is not None and slot not in requested_variant_slots:
            retry_paths = []
        elif mode == "live" and requested_variant_slots is not None:
            retry_paths = (
                retry_paths[:live_variant_max_total]
                if trusted_frame_by_matches or trusted_frame_by_quality
                else []
            )
        elif mode == "live":
            slot_quality = (
                frame_quality_slots[index]
                if index < len(frame_quality_slots) and isinstance(frame_quality_slots[index], dict)
                else {}
            )
            retry_paths = retry_paths[
                :live_variant_budget_for_payload(
                    payload,
                    alias_map,
                    trusted_frame_by_matches=trusted_frame_by_matches,
                    trusted_frame_by_quality=trusted_frame_by_quality,
                    trusted_slot_by_quality=bool(
                        slot_quality.get(
                            "trusted_title_region",
                            slot_quality.get("title_band_like"),
                        )
                    ),
                )
            ]
        elif not needs_variant_retry(payload, alias_map):
            retry_paths = []
        desired_retry_paths[index] = retry_paths

    if mode == "live":
        retry_paths_by_slot: List[List[str]] = [[] for _ in payloads]
        for variant_offset in range(max((len(paths) for paths in desired_retry_paths), default=0)):
            for index, paths in enumerate(desired_retry_paths):
                if variant_offset >= len(paths):
                    continue
                retry_paths_by_slot[index].append(paths[variant_offset])
                if sum(len(items) for items in retry_paths_by_slot) >= live_variant_max_total:
                    break
            if sum(len(items) for items in retry_paths_by_slot) >= live_variant_max_total:
                break
    else:
        retry_paths_by_slot = desired_retry_paths

    retry_jobs: List[tuple[int, List[str]]] = []
    for index, retry_paths in enumerate(retry_paths_by_slot):
        if not retry_paths:
            continue
        retry_rects = []
        if index < len(variant_rect_groups):
            retry_rects = variant_rect_groups[index][1 : 1 + len(retry_paths)]
        for retry_path, retry_rect in zip(retry_paths, retry_rects):
            path = Path(retry_path)
            if not path.exists():
                lazy_variant_crops.append((path, Rect(**retry_rect)))
        retry_jobs.append((index, retry_paths))

    inference_payloads = list(payloads)
    if retry_jobs:
        if lazy_variant_crops:
            if source_image is not None:
                for path, rect in lazy_variant_crops:
                    crop_rect_from_image(
                        source_image,
                        rect,
                        path,
                        compress_level=capture_png_compress_level_for_mode(
                            performance_mode
                        ),
                    )
            else:
                with Image.open(image_path) as loaded_source_image:
                    loaded_source_image.load()
                    for path, rect in lazy_variant_crops:
                        crop_rect_from_image(
                            loaded_source_image,
                            rect,
                            path,
                            compress_level=capture_png_compress_level_for_mode(
                                performance_mode
                            ),
                        )
        flat_retry_paths = [path for _, paths in retry_jobs for path in paths]
        variant_budget_used = len(flat_retry_paths)
        retry_payloads = normalize_payload_count(
            runner(flat_retry_paths, backend),
            len(flat_retry_paths),
            [Path(path) for path in flat_retry_paths],
        )
        mark("variant_ocr_ms", timings)
        offset = 0
        for index, paths in retry_jobs:
            group_payloads = [payloads[index]] + retry_payloads[offset: offset + len(paths)]
            for variant_offset, (path, retry_payload) in enumerate(
                zip(paths, retry_payloads[offset: offset + len(paths)]),
                start=1,
            ):
                slot_candidates[index].append(
                    payload_candidate(index + 1, path, retry_payload, None, variant_offset)
                )
            inference_payloads[index] = choose_best_payload(group_payloads, None)
            payloads[index]["candidates"] = slot_candidates[index]
            offset += len(paths)
    else:
        timings["variant_ocr_ms"] = 0

    for index, payload in enumerate(payloads):
        if "candidates" not in payload and index < len(slot_candidates):
            payload["candidates"] = slot_candidates[index]
    ocr_inference = infer_augment_frame_from_payloads(inference_payloads, None)
    mark("match_ms", timings)
    timings["total_ms"] = round((time.perf_counter() - started_at) * 1000)
    return {
        "preset": preset.to_dict(),
        "crop_info": crop_info,
        "anchor_detection": anchor_detection.to_dict(),
        "ocr_inferred_augment_frame": bool(ocr_inference["detected"]),
        "ocr_inference": ocr_inference,
        "timings": timings,
        "slot_candidates": slot_candidates,
        "variant_budget_used": variant_budget_used,
        "variant_slots_requested": sorted(requested_variant_slots or []),
        "worker_backend": "pipeline",
        "performance_mode": performance_mode,
        "frame_quality": frame_quality,
        "payloads": payloads,
    }


def execute_cycle(
    hero: str,
    image_path: Path,
    preset_key: str,
    *,
    data_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    backend: str = "auto",
) -> Dict[str, Any]:
    runtime = load_runtime(data_dir)
    run = run_three_box_ocr(image_path, preset_key, output_dir=output_dir, backend=backend)
    ocr_texts = [
        payload["lines"][0]["text"] if payload.get("lines") else ""
        for payload in run["payloads"]
    ]
    ocr_matches = match_ocr_texts(ocr_texts, runtime["augment_aliases"])
    matched_names = [item["matched_name"] for item in ocr_matches if item["matched_name"]]
    recommendations = recommend_augments(runtime, hero, matched_names)
    return {
        "hero": hero,
        "runtime_meta": runtime["meta"],
        "preset": run["preset"],
        "crop_info": run["crop_info"],
        "anchor_detection": run["anchor_detection"],
        "ocr_payloads": run["payloads"],
        "ocr_matches": ocr_matches,
        "top_augments": recommendations,
        "top_items": recommend_items(runtime, hero),
    }


def resolve_frame_sources(
    screen_images: Sequence[Path],
    capture_live: bool,
    preset_key: str,
    frames: int,
) -> List[Path]:
    if screen_images:
        items = [path.resolve() for path in screen_images]
        if len(items) == 1 and frames > 1:
            return items * frames
        return items

    if capture_live:
        output_dir = ensure_output_dir(project_root() / "artifacts" / "captures" / preset_key)
        result: List[Path] = []
        for index in range(max(frames, 1)):
            frame_path = output_dir / f"capture_{index + 1}.png"
            result.append(capture_screen(frame_path))
        return result

    raise ValueError("screen_images or capture_live is required for auto mode")


def run_auto_watch(
    hero: str,
    preset_key: str,
    *,
    screen_images: Sequence[Path],
    data_dir: Optional[Path] = None,
    frames: int = 2,
    capture_live: bool = False,
    interval_ms: int = 0,
    backend: str = "auto",
) -> Dict[str, Any]:
    watcher = AutoWatcher()
    frame_paths = resolve_frame_sources(screen_images, capture_live, preset_key, frames)
    frame_results: List[Dict[str, Any]] = []

    watcher.state = "watching_anchor"
    for index, frame_path in enumerate(frame_paths, start=1):
        cycle_result = execute_cycle(
            hero,
            frame_path,
            preset_key,
            data_dir=data_dir,
            output_dir=project_root() / "artifacts" / "captures" / preset_key / f"frame_{index}",
            backend=backend,
        )
        frame_results.append(cycle_result)
        watcher.observe(index, cycle_result)
        if watcher.state == "stable_result_ready":
            break
        if interval_ms > 0 and index < len(frame_paths):
            time.sleep(interval_ms / 1000.0)

    return {
        "hero": hero,
        "preset_key": preset_key,
        "mode": "auto",
        "state": watcher.state,
        "frames_processed": len(frame_results),
        "state_history": watcher.history,
        "stable_result": watcher.stable_result,
        "frame_results": frame_results,
        "failures": watcher.failures,
    }
