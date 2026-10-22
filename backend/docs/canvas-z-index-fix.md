# 画布节点 Z-Index 修复

## 问题描述

在无限画布分镜四媒体生成验收（AN 轮次）中发现：成员用户无法点击视频播放按钮。

**根本原因**：后生成的图片节点使用了更高的 z-index，覆盖了先生成的视频节点，导致视频播放控制按钮被图片遮挡。

## 技术细节

### 原始行为（已修复前的假设）

在 `canvas_service.py:770` 中，节点的 z-index 简单地使用数组索引：

```python
z_index = index  # 0, 1, 2, 3, ...
```

这导致：
- 第 1 个节点（图片）：z-index = 0
- 第 2 个节点（图片）：z-index = 1  
- 第 3 个节点（视频）：z-index = 2
- 第 4 个节点（图片）：z-index = 3  ← 覆盖视频！

### 修复方案

实现了媒体类型感知的 z-index 计算（`canvas_service.py:758-763`）：

```python
def _calculate_node_z_index(index: int, node_data: dict) -> int:
    """
    计算节点的 z-index，视频节点使用更高层级。
    
    这确保视频播放按钮不会被同坐标的图片节点遮挡。
    视频节点使用 10000+ 的 z-index 基准，图片等其他节点使用原始索引。
    """
    node_type = node_data.get("type", "")
    if node_type == "media":
        content = node_data.get("content", {})
        if isinstance(content, dict) and content.get("mediaType") == "video":
            return index + 10000
    return index
```

### 修复后的行为

同样的节点序列：
- 第 1 个节点（图片）：z-index = 0
- 第 2 个节点（图片）：z-index = 1
- 第 3 个节点（视频）：z-index = 10002  ← 在独立的高层级
- 第 4 个节点（图片）：z-index = 3

**结果**：视频始终在所有图片之上，播放按钮可点击。

## 测试覆盖

创建了 `tests/unit/test_canvas_node_z_index.py`，包含以下测试场景：

1. **基础逻辑**：视频节点 z-index = index + 10000，其他节点 = index
2. **非媒体节点**：文本、形状等使用常规 z-index
3. **边界情况**：空 content、非 video mediaType、大索引值
4. **相对顺序保持**：多个视频之间、多个图片之间保持原始顺序
5. **真实分镜场景**：标题文本 + 背景图 + 前景图 + 视频

## 影响范围

- **后端**：`canvas_service.py` 的 `_apply_document` 方法
- **数据库**：`canvas_nodes.z_index` 字段的值分布改变
- **前端**：无需修改，CSS `z-index` 属性自动生效
- **成员视图**：视频播放按钮现在可以正常点击

## 验证步骤

1. 单元测试：`pytest tests/unit/test_canvas_node_z_index.py -v`
2. 完整测试套件：`pytest tests/unit tests/api -q`
3. 代码格式：`ruff check` 和 `ruff format --check`
4. 集成验证：执行新的验收轮次，确认成员可以播放视频

## 后续工作

此修复解决了 AN 验收中的视频播放问题。下一步：

1. 执行 ModelHub 预算扩额（5 posts / 30 seconds）
2. 创建新验收轮次（AO）
3. 完整四媒体验收：2 图片 + 2 视频
4. 成员播放验证

## 参考

- 交接文档：`docs/plans/2026-10/2026-10-12-BeefTV功能进度交接清单.md` U01 部分
- 探索计划：`.claude/plans/elegant-booping-waffle-agent-a1f17a12a3fbe53ea.md`
- 相关代码：`backend/src/short_drama/service/canvas_service.py:758-785`
