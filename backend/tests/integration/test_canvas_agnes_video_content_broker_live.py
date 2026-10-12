"""独立输入示例：原UI与正式队列生成无印刷红色纸盒，不替换旧验收记录。"""

from tests.integration.test_canvas_agnes_broker_live import PROMPTS
from tests.integration.test_canvas_agnes_broker_live import pytestmark as pytestmark
from tests.integration.test_canvas_agnes_broker_live import (
    test_original_canvas_real_agnes_through_formal_generation_worker as run_original,
)
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import identity_app as identity_app

UNPRINTED_BOX_PROMPT = (
    "A locked-off camera records a single plain matte red cube-shaped cardboard box with a "
    "simple flat fitted lid slowly rotating in place on a completely white studio tabletop. "
    "The box has uniformly solid red surfaces, like an unprinted geometric prop, with no "
    "ribbon, bow, wrapping pattern, decorative seal or retail packaging. "
    "Every visible surface is blank and unmarked. Only the box and its soft shadow appear "
    "against the seamless white studio background. Soft neutral studio lighting. "
    "Keep the entire box visible throughout the four-second continuous shot. "
    "No people or other objects. Absolutely no text, letters, numbers, logos, symbols, "
    "labels, branding, watermarks or markings anywhere in the frame."
)


def test_original_canvas_unprinted_red_box_real_video_at_390(resource_app, tmp_path, monkeypatch):
    monkeypatch.setitem(PROMPTS, "video", UNPRINTED_BOX_PROMPT)
    run_original(resource_app, tmp_path, monkeypatch, "video", 390)
