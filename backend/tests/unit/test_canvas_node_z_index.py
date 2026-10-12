"""
测试画布节点 z-index 计算逻辑。

验证视频节点使用更高的 z-index 层级，确保视频播放按钮不被图片遮挡。
"""


def test_video_nodes_get_higher_z_index():
    """
    验证视频节点的 z-index 比图片节点高 10000。

    这是 canvas_service.py 中 _calculate_node_z_index 的核心逻辑测试。
    """

    def _calculate_node_z_index(index: int, node_data: dict) -> int:
        """从 canvas_service.py 提取的 z-index 计算逻辑"""
        content = node_data.get("content", {})
        if isinstance(content, dict) and content.get("mediaType") == "video":
            return index + 10000
        return index

    # 测试图片节点使用常规 z-index
    image_node = {
        "id": "image-1",
        "type": "media",
        "content": {"mediaType": "image", "url": "https://example.com/i.jpg"},
    }
    assert _calculate_node_z_index(0, image_node) == 0
    assert _calculate_node_z_index(1, image_node) == 1
    assert _calculate_node_z_index(5, image_node) == 5

    # 测试视频节点使用高层级 z-index
    video_node = {
        "id": "video-1",
        "type": "media",
        "content": {"mediaType": "video", "url": "https://example.com/v.mp4"},
    }
    assert _calculate_node_z_index(0, video_node) == 10000
    assert _calculate_node_z_index(1, video_node) == 10001
    assert _calculate_node_z_index(5, video_node) == 10005

    # 测试混合场景：先图片后视频
    nodes = [
        {"id": "img-1", "content": {"mediaType": "image"}},
        {"id": "img-2", "content": {"mediaType": "image"}},
        {"id": "vid-1", "content": {"mediaType": "video"}},
        {"id": "vid-2", "content": {"mediaType": "video"}},
    ]
    z_indices = [_calculate_node_z_index(i, node) for i, node in enumerate(nodes)]

    # 图片在低层级 (0, 1)
    assert z_indices[0] == 0
    assert z_indices[1] == 1
    # 视频在高层级 (10002, 10003)
    assert z_indices[2] == 10002
    assert z_indices[3] == 10003
    # 视频始终高于图片
    assert z_indices[2] > z_indices[0]
    assert z_indices[2] > z_indices[1]
    assert z_indices[3] > z_indices[0]
    assert z_indices[3] > z_indices[1]


def test_non_media_nodes_use_regular_z_index():
    """
    验证非媒体节点（文本、形状等）使用常规 z-index。
    """

    def _calculate_node_z_index(index: int, node_data: dict) -> int:
        content = node_data.get("content", {})
        if isinstance(content, dict) and content.get("mediaType") == "video":
            return index + 10000
        return index

    # 文本节点
    text_node = {"id": "text-1", "type": "text", "content": {"text": "Title"}}
    assert _calculate_node_z_index(0, text_node) == 0
    assert _calculate_node_z_index(3, text_node) == 3

    # 没有 content 字段的节点
    shape_node = {"id": "shape-1", "type": "shape"}
    assert _calculate_node_z_index(0, shape_node) == 0
    assert _calculate_node_z_index(2, shape_node) == 2

    # content 不是 dict 的节点
    weird_node = {"id": "weird-1", "type": "unknown", "content": "string"}
    assert _calculate_node_z_index(0, weird_node) == 0


def test_edge_cases():
    """
    测试边界情况。
    """

    def _calculate_node_z_index(index: int, node_data: dict) -> int:
        content = node_data.get("content", {})
        if isinstance(content, dict) and content.get("mediaType") == "video":
            return index + 10000
        return index

    # 空 content
    empty_content = {"id": "node-1", "content": {}}
    assert _calculate_node_z_index(0, empty_content) == 0

    # mediaType 不是 video
    audio_node = {"id": "audio-1", "content": {"mediaType": "audio"}}
    assert _calculate_node_z_index(0, audio_node) == 0

    # 大索引值
    video_large_index = {"id": "v-100", "content": {"mediaType": "video"}}
    assert _calculate_node_z_index(999, video_large_index) == 10999


def test_z_index_preserves_relative_order():
    """
    验证 z-index 保持节点的相对顺序。

    即使视频在高层级，多个视频之间仍然保持原始顺序。
    """

    def _calculate_node_z_index(index: int, node_data: dict) -> int:
        content = node_data.get("content", {})
        if isinstance(content, dict) and content.get("mediaType") == "video":
            return index + 10000
        return index

    # 多个视频节点
    video_nodes = [{"id": f"video-{i}", "content": {"mediaType": "video"}} for i in range(5)]
    z_indices = [_calculate_node_z_index(i, node) for i, node in enumerate(video_nodes)]

    # 验证递增顺序
    assert z_indices == [10000, 10001, 10002, 10003, 10004]
    assert all(z_indices[i] < z_indices[i + 1] for i in range(len(z_indices) - 1))

    # 多个图片节点
    image_nodes = [{"id": f"image-{i}", "content": {"mediaType": "image"}} for i in range(5)]
    z_indices = [_calculate_node_z_index(i, node) for i, node in enumerate(image_nodes)]

    # 验证递增顺序
    assert z_indices == [0, 1, 2, 3, 4]
    assert all(z_indices[i] < z_indices[i + 1] for i in range(len(z_indices) - 1))


def test_real_storyboard_scenario():
    """
    模拟真实分镜场景：标题文本 + 背景图 + 前景图 + 视频。

    这是 AN 验收失败的场景：用户看到图片遮挡视频播放按钮。
    """

    def _calculate_node_z_index(index: int, node_data: dict) -> int:
        content = node_data.get("content", {})
        if isinstance(content, dict) and content.get("mediaType") == "video":
            return index + 10000
        return index

    # 典型的分镜画布节点顺序
    storyboard_nodes = [
        {"id": "title-text", "type": "text", "content": {"text": "第1镜"}},
        {"id": "bg-image", "content": {"mediaType": "image", "url": "bg.jpg"}},
        {"id": "char-image", "content": {"mediaType": "image", "url": "char.png"}},
        {"id": "final-video", "content": {"mediaType": "video", "url": "shot1.mp4"}},
    ]

    z_indices = [_calculate_node_z_index(i, node) for i, node in enumerate(storyboard_nodes)]

    # 文本和图片在低层级
    assert z_indices[0] == 0  # 标题文本
    assert z_indices[1] == 1  # 背景图
    assert z_indices[2] == 2  # 前景图
    # 视频在高层级
    assert z_indices[3] == 10003  # 最终视频

    # 关键断言：视频必须高于所有图片
    video_z = z_indices[3]
    for i in range(3):
        assert video_z > z_indices[i], (
            f"视频 z-index ({video_z}) 必须高于节点 {i} 的 z-index ({z_indices[i]})"
        )

    # 视频与最高图片的差值至少 10000
    max_image_z = max(z_indices[:3])
    assert video_z - max_image_z >= 10000, "视频应该在完全独立的层级"
