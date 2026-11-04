from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="读取已获准图位或画布节点对应图片的像素内容，供模型实际观察。",
    when_to_use=(
        "需要比较构图、辨认内容或基于图片像素作判断时调用；素材/分镜图先从 read_image_group "
        "取得真实引用参数。"
    ),
    avoid="不要传入 URL、存储路径或猜测的 media_id；仅知道图组目录不代表已看过图片。",
    prerequisites=(
        "标准目标须提供其真实 project_id、target_kind、target_id、group_id、item_"
        "id 和目录中的 media_id；画布节点须提供 canvas_id、节点身份和获准媒体 ID。purpose "
        "写明观察目的。"
    ),
    effect=(
        "只读；图片存储定位由服务端解析并校验，不接受客户端定位。detail 选择像素交付级别，"
        "crop 是正宽高的图像坐标框。"
    ),
    returns="返回受控图像引用，框架水合后提供图像像素；不会采用图片或修改作品。",
    recovery="若引用过期或不属于获准目标，重新读取图组/节点目录并使用返回的真实身份。",
    example='{"project_id":"375104027219726336","target_kind":"shot","target_id":"375104027219726500","group_id":"375104027219726510","item_id":"375104027219726520","media_id":"375104027219726530","purpose":"核对人物位置和画面文字","detail":"high"}',
).render()
