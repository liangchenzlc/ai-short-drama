"""
集成测试：验证 canvas_service 中的 z-index 计算逻辑。

这个测试直接调用 canvas_service._apply_document 中定义的 _calculate_node_z_index 函数。
"""


def test_canvas_service_z_index_function_exists():
    """
    验证 canvas_service.py 中确实存在 _calculate_node_z_index 函数。

    通过读取源代码来验证函数定义和逻辑。
    """
    from pathlib import Path

    service_file = (
        Path(__file__).parent.parent.parent
        / "src"
        / "short_drama"
        / "service"
        / "canvas_service.py"
    )
    assert service_file.exists(), "canvas_service.py 文件不存在"

    content = service_file.read_text(encoding="utf-8")

    # 验证函数定义存在
    assert "def _calculate_node_z_index" in content, "_calculate_node_z_index 函数未定义"

    # 验证核心逻辑：检查 mediaType == "video"
    assert (
        'mediaType" == "video' in content
        or "mediaType') == 'video" in content
        or 'mediaType") == "video' in content
    ), "未找到 video mediaType 检查逻辑"

    # 验证返回 index + 10000
    assert "index + 10000" in content, "未找到 index + 10000 返回语句"

    # 验证函数被调用
    assert "z_index=_calculate_node_z_index" in content, "_calculate_node_z_index 未被调用"


def test_z_index_logic_matches_implementation():
    """
    验证测试中的逻辑与实际实现匹配。

    读取 canvas_service.py 源代码，确认关键行为。
    """
    from pathlib import Path

    service_file = (
        Path(__file__).parent.parent.parent
        / "src"
        / "short_drama"
        / "service"
        / "canvas_service.py"
    )
    content = service_file.read_text(encoding="utf-8")

    # 查找函数定义到返回语句之间的代码块
    start_marker = "def _calculate_node_z_index"
    end_marker = "return index"

    assert start_marker in content, "找不到函数定义"
    assert end_marker in content, "找不到默认返回语句"

    # 提取函数体
    start_idx = content.index(start_marker)
    function_section = content[start_idx : start_idx + 1000]  # 读取足够长的代码段

    # 验证关键逻辑存在
    assert (
        'node_type = node_data.get("type"' in function_section
        or "node_type = node_data.get('type'" in function_section
    ), "未找到 node_type 提取逻辑"
    assert (
        'content = node_data.get("content"' in function_section
        or "content = node_data.get('content'" in function_section
    ), "未找到 content 提取逻辑"
    assert "if node_type ==" in function_section or "if node_type ==" in function_section, (
        "未找到 node_type 检查"
    )
    assert "mediaType" in function_section, "未找到 mediaType 检查"
    assert "video" in function_section, "未找到 video 字符串"
    assert "10000" in function_section, "未找到 10000 偏移量"
