# Third-party notices

Vaquero is MIT licensed (see `LICENSE`). The app bundles the libraries below, each under its own license.

| Component | License | Source |
|---|---|---|
| Python 3.12 | PSF License | https://www.python.org/ |
| Qt 6 and PySide6 | LGPL-3.0 | https://www.qt.io/ · https://wiki.qt.io/Qt_for_Python |
| ONNX Runtime | MIT | https://github.com/microsoft/onnxruntime |
| OpenCV (opencv-python-headless) | Apache-2.0; its bundled FFmpeg is LGPL-2.1 | https://github.com/opencv/opencv-python |
| NumPy | BSD-3-Clause | https://numpy.org/ |

Qt, PySide6 and FFmpeg are linked dynamically and ship as separate libraries inside `Vaquero.app/Contents/Frameworks`, so they can be replaced with other builds of the same version. Their source code is available from the links above.

## Face model

The face model (`buffalo_l`: SCRFD detection and ArcFace recognition) is not part of Vaquero and is not covered by its license. The app downloads it on first launch from the [InsightFace](https://github.com/deepinsight/insightface) project's own release. InsightFace's code is MIT licensed; its pretrained models are licensed for non-commercial research use only.
