"""标准模式的无模型图片处理；边界取整和表情融合与画布保持一致。"""

import math
from io import BytesIO

from PIL import Image

from short_drama.core.exceptions import WorkflowError


def decode(data: bytes) -> Image.Image:
    try:
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError("pixels")
            image.load()
            return image.convert("RGBA")
    except (OSError, ValueError, Image.DecompressionBombError):
        raise WorkflowError("invalid_image", "图片无法解码或超过 4000 万像素", 422) from None


def png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def crop_box(size, parameters):
    width, height = size
    x, y = math.floor(parameters["x"] * width), math.floor(parameters["y"] * height)
    right = min(width, x + max(1, math.ceil(parameters["width"] * width)))
    bottom = min(height, y + max(1, math.ceil(parameters["height"] * height)))
    return min(x, width - 1), min(y, height - 1), right, bottom


def process(data: bytes, kind: str, parameters: dict) -> list[bytes]:
    image = decode(data)
    if kind == "crop":
        return [png(image.crop(crop_box(image.size, parameters)))]
    if kind == "resize":
        filters = {
            "nearest": Image.Resampling.NEAREST,
            "bilinear": Image.Resampling.BILINEAR,
            "high": Image.Resampling.LANCZOS,
        }
        return [
            png(
                image.resize(
                    (parameters["output_width"], parameters["output_height"]),
                    filters[parameters["interpolation"]],
                )
            )
        ]
    if kind == "split":
        rows, columns = parameters["rows"], parameters["columns"]
        if image.width < columns or image.height < rows:
            raise WorkflowError("image_too_small", "图片像素不足以按所选宫格切分", 422)
        return [
            png(
                image.crop(
                    (
                        column * image.width // columns,
                        row * image.height // rows,
                        (column + 1) * image.width // columns,
                        (row + 1) * image.height // rows,
                    )
                )
            )
            for row in range(rows)
            for column in range(columns)
        ]
    raise WorkflowError("unsupported_image_operation", "不支持此图片处理操作", 422)


def emotion_geometry(size, parameters):
    left, top, right, bottom = crop_box(size, parameters)
    fw, fh = right - left, bottom - top
    x = math.floor(max(0, left - fw * 0.85))
    y = math.floor(max(0, top - fh * 0.75))
    region = [
        x,
        y,
        math.ceil(min(size[0], left + fw * 1.85)),
        math.ceil(min(size[1], top + fh * 2)),
    ]
    ellipse = [
        left - x + fw / 2,
        top - y - fh * 0.28 + fh * 1.48 / 2,
        max(1, fw * 1.44 / 2),
        max(1, fh * 1.48 / 2),
    ]
    return region, ellipse


def ellipse_distance(x, y, ellipse):
    cx, cy, rx, ry = ellipse
    return math.hypot((x + 0.5 - cx) / rx, (y + 0.5 - cy) / ry)


def emotion_artifacts(data, parameters):
    image = decode(data)
    region, ellipse = emotion_geometry(image.size, parameters)
    crop = image.crop(region)
    mask = Image.new("RGBA", crop.size, "white")
    pixels = mask.load()
    for y in range(crop.height):
        for x in range(crop.width):
            if ellipse_distance(x, y, ellipse) <= 1:
                pixels[x, y] = (255, 255, 255, 0)
    identity = image.crop(crop_box(image.size, parameters))
    return [png(crop), png(identity), png(mask)], {"region": region, "ellipse": ellipse}


def composite_emotion(source_data, generated_data, geometry):
    """只融合椭圆内像素；边缘校色、smoothstep 羽化与画布采用同一公式。"""
    image = decode(source_data)
    region, ellipse = geometry["region"], geometry["ellipse"]
    source = image.crop(region)
    generated = decode(generated_data).resize(source.size, Image.Resampling.LANCZOS)
    original, edited = source.load(), generated.load()
    source_sum, edited_sum, count = [0, 0, 0], [0, 0, 0], 0
    for y in range(source.height):
        for x in range(source.width):
            if 0.72 <= ellipse_distance(x, y, ellipse) <= 0.96:
                for channel in range(3):
                    source_sum[channel] += original[x, y][channel]
                    edited_sum[channel] += edited[x, y][channel]
                count += 1
    gains = [
        max(0.78, min(1.22, a / max(1, b))) for a, b in zip(source_sum, edited_sum, strict=True)
    ]
    if count < 32:
        gains = [1, 1, 1]
    for y in range(source.height):
        for x in range(source.width):
            distance = ellipse_distance(x, y, ellipse)
            if distance >= 1:
                continue
            t = max(0, (distance - 0.7) / 0.3)
            alpha = 1 - t * t * (3 - 2 * t)
            old, new = original[x, y], edited[x, y]
            channels = []
            for channel in range(3):
                gain = 1 + (gains[channel] - 1) * (0.42 + (1 - alpha) * 0.58)
                corrected = max(0, min(255, new[channel] * gain))
                channels.append(math.floor(old[channel] * (1 - alpha) + corrected * alpha + 0.5))
            channels.append(math.floor(old[3] * (1 - alpha) + new[3] * alpha + 0.5))
            original[x, y] = tuple(channels)
    image.paste(source, (region[0], region[1]))
    return png(image)
