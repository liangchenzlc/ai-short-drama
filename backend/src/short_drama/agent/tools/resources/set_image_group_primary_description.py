from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="把已就绪的真实图位明确采用为角色、场景、道具或分镜的当前主图。",
    when_to_use="用户明确要求采用图片，或任务目标明确要求设置主图，并已读取并核对该图位时调用。",
    avoid="不要只凭生成顺序猜主图；不要把未就绪图位或未授权图位设为主图。",
    prerequisites=(
        "project_id、target 和 group/item ID "
        "使用图组目录的真实身份；expected_primary_version 来自图组，expected_item_version "
        "来自图位，expected_target_version 来自主体。"
    ),
    effect="写入主图选择并推进相关版本；成功后才表示作品已采用该图像。",
    returns="返回 adopted 状态、result_refs、changes 和新主图/主体版本。",
    recovery="任一版本冲突时重新读取图片组及目标，向用户呈现变化后再决定；不要重放旧版本。",
    example='{"project_id":"375104027219726336","target_kind":"shot","target_id":"375104027219726500","item_id":"375104027219726520","expected_primary_version":"1","expected_item_version":"1","expected_target_version":"2","group_id":"375104027219726510"}',
).render()
