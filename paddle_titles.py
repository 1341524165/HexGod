"""PP-OCRv5 detector + recognizer for ordered title bands.

Models, dictionary and region layout match aramgg_client's bundled pipeline.
Detection separates rarity labels / wrapped text before CTC recognition. No
grayscale conversion and no fixed-width squeezing of a multi-line title band.
"""
from collections import OrderedDict
from pathlib import Path
import hashlib
import json
import re
import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image


class TitleOcr:
    def __init__(self, folder=None):
        folder = folder or Path(__file__).resolve().parent / 'models' / 'paddleocr'
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.det = ort.InferenceSession(str(folder / 'det/inference.onnx'), options, providers=['CPUExecutionProvider'])
        self.rec = ort.InferenceSession(str(folder / 'rec/inference.onnx'), options, providers=['CPUExecutionProvider'])
        self.characters = ['']
        inside = False
        for line in (folder / 'rec/inference.yml').read_text('utf-8').splitlines():
            if re.match(r'\s*character_dict:\s*$', line):
                inside = True
                continue
            if not inside:
                continue
            if line and not line[0].isspace():
                break
            match = re.match(r'\s*-\s?(.*)$', line)
            if match:
                value = match[1]
                if value.startswith("'") and value.endswith("'"):
                    value = value[1:-1].replace("''", "'")
                elif value.startswith('"') and value.endswith('"'):
                    value = json.loads(value)
                self.characters.append(value)
        self.characters.append(' ')  # PP-OCR CTC use_space_char.
        self.cache = OrderedDict()

    def detect(self, bgr):
        height, width = bgr.shape[:2]
        ratio = min(1, 960 / max(height, width))
        dw, dh = [max(32, round(n * ratio / 32) * 32) for n in (width, height)]
        scaled = cv2.resize(bgr, (dw, dh)).astype(np.float32) / 255
        scaled = (scaled - np.array([.485, .456, .406], np.float32)) / np.array([.229, .224, .225], np.float32)
        tensor = np.ascontiguousarray(scaled.transpose(2, 0, 1)[None])
        probability = self.det.run(None, {self.det.get_inputs()[0].name: tensor})[0][0, 0]
        mask = (probability > .55).astype(np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w * h < 24 or w < 3 or h < 3:
                continue
            if float(probability[y:y+h, x:x+w][mask[y:y+h, x:x+w] > 0].mean()) < .6:
                continue
            # Titles are horizontal. Expand the DB inner text box to retain
            # anti-aliased strokes without swallowing the next text row.
            pad_x, pad_y = h * .5, h * .3
            boxes.append([max(0, int((x-pad_x)*width/dw)), max(0, int((y-pad_y)*height/dh)),
                          min(width, int((x+w+pad_x)*width/dw)), min(height, int((y+h+pad_y)*height/dh))])
        return sorted(boxes, key=lambda b: (b[1], b[0]))

    def recognize(self, bgr):
        height, width = bgr.shape[:2]
        target = max(32, min(1280, round(48 * width / max(1, height))))
        pixels = cv2.resize(bgr, (target, 48)).astype(np.float32) / 127.5 - 1
        tensor = np.ascontiguousarray(pixels.transpose(2, 0, 1)[None])
        output = self.rec.run(None, {self.rec.get_inputs()[0].name: tensor})[0][0]
        if output.shape[-1] != len(self.characters):
            raise ValueError('OCR 模型与字符字典不匹配')
        ids, scores = output.argmax(axis=-1), output.max(axis=-1)
        keep = (ids != 0) & np.r_[True, ids[1:] != ids[:-1]]
        text = ''.join(self.characters[i] for i in ids[keep])
        return text, float(scores[keep].mean()) if keep.any() else 0

    def __call__(self, paths):
        results = []
        for path in paths:
            with Image.open(path) as image:
                rgb = np.asarray(image.convert('RGB'))
            key = hashlib.sha256(rgb.tobytes()).hexdigest()
            if key in self.cache:
                results.append(self.cache[key]); self.cache.move_to_end(key)
                continue
            bgr = np.ascontiguousarray(rgb[:, :, ::-1])
            lines = []
            for box in self.detect(bgr):
                x1, y1, x2, y2 = box
                text, score = self.recognize(bgr[y1:y2, x1:x2])
                if score < .97:
                    padding = max(2, round((y2-y1) * .22))
                    expanded = bgr[max(0,y1-padding):min(bgr.shape[0],y2+padding),
                                   max(0,x1-padding):min(bgr.shape[1],x2+padding)]
                    retry, confidence = self.recognize(expanded)
                    if confidence > score:
                        text, score = retry, confidence
                lines.append({'text': text, 'score': score, 'box': box})
            result = {'lines': lines, 'backend': 'PP-OCRv5-det-rec'}
            self.cache[key] = result
            if len(self.cache) > 48:
                self.cache.popitem(last=False)
            results.append(result)
        return results
