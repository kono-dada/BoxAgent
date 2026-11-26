"""Check exact parity with the installed Qwen image processor using the vision interpreter."""

import runpy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from mlx_vlm.models.qwen3_vl.processing_qwen3_vl import Qwen3VLImageProcessor
from PIL import Image


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    adapter = runpy.run_path(str(repo / "scripts/vision_inputs.py"))
    original = Qwen3VLImageProcessor()
    processor = SimpleNamespace(image_processor=original)
    adapter["enable_image_inputs"](processor)
    assert isinstance(processor.image_processor, adapter["QwenImageInputs"])
    rng = np.random.default_rng(7881)
    for height, width in ((32, 64), (87, 145), (393, 701), (720, 1280)):
        pixels = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
        pixels.reshape(-1)[:256] = np.arange(256, dtype=np.uint8)
        image = Image.fromarray(pixels)
        expected = original(images=[image])
        scoped = adapter["for_review"](processor).image_processor
        for actual in (processor.image_processor([image]), scoped([image]), scoped([image])):
            assert actual.keys() == expected.keys()
            for key, reference in expected.items():
                np.testing.assert_array_equal(np.asarray(actual[key]), reference, strict=True)
    print("Pixel tensors and image grids matched for four RGB images, including resize and retry.")


if __name__ == "__main__":
    main()
