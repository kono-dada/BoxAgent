"""桌宠缩放偏好和屏幕内的窗口布局。"""

import json
import math


PET_MIN_SCALE = 0.5
PET_MAX_SCALE = 6.0


def valid_pet_scale(scale):
    """接受范围内的连续比例，拒绝布尔值与非有限数。"""
    return type(scale) in (int, float) and PET_MIN_SCALE <= scale <= PET_MAX_SCALE


def load_pet_scale(data_dir):
    """旧安装或损坏的设置使用原始大小。"""
    try:
        settings = json.loads((data_dir / "appearance.json").read_text())
        scale = settings.get("scale") if isinstance(settings, dict) else None
        if valid_pet_scale(scale):
            return float(scale)
    except (OSError, ValueError):
        pass
    return 1.0


def save_pet_scale(data_dir, scale):
    """替换完整设置文件，避免中途退出留下半份内容。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "appearance.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"scale": scale}))
    temporary.replace(target)


def scaled_pet_size(base_size, scale, screen_size):
    """保持长宽比，并让小屏幕仍能容纳整个角色。"""
    width, height = base_size
    fitted_scale = min(scale, screen_size[0] / width, screen_size[1] / height)
    return width * fitted_scale, height * fitted_scale


def resized_pet_frame(frame, size, screen):
    """以底部中心缩放，只在接近屏幕边缘时调整位置。"""
    (x, y), (old_width, _old_height) = frame
    (screen_x, screen_y), (screen_width, screen_height) = screen
    width, height = size
    x += (old_width - width) / 2
    x = max(screen_x, min(x, screen_x + screen_width - width))
    y = max(screen_y, min(y, screen_y + screen_height - height))
    return ((x, y), (width, height))


def validated_render_bounds(message, viewport):
    """只接受当前视口内的有效边界，忽略缩放之前的异步消息。"""
    if not message or tuple(message.get("viewport", ())) != tuple(viewport):
        return None
    bounds = message.get("bounds", {})
    values = [bounds.get(key) for key in ("x", "y", "width", "height")]
    # WebKit 的数值经 PyObjC 桥接后是 float 子类，不能用精确类型判断。
    if any(not isinstance(v, (int, float)) or isinstance(v, bool)
           or not math.isfinite(v) for v in values):
        return None
    x, y, width, height = values
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        return None
    if x + width > viewport[0] or y + height > viewport[1]:
        return None
    return ((x, y), (width, height))
