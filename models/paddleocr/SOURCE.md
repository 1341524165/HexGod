# OCR model provenance

Local source: `reference/aramgg_client/resources/paddleocr/`.
Repository: https://github.com/valkia/aramgg_client
Inspected commit: e07cef86e7f88a16aa6046eabfc4cc7c4ab8da5a (client 0.2.17).

The det/rec ONNX files and rec/inference.yml are unchanged copies of that
reference checkout. hexgod uses onnxruntime CPU execution, OpenCV for detection
postprocessing, and the exact recognition character dictionary including the
CTC blank and trailing space token. Runtime versions: ../../requirements-ocr.txt.

The source client code examined for integration is src/main/image-analyzer.ts,
src/shared/augment-ranking.ts and scripts/fetch-client-data.mjs. The WPF passive
click confirmation and local match journal are hexgod implementations.
