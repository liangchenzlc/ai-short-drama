from typing import Annotated, Literal

from pydantic import Field

from short_drama.schemas.assistant.memory.memory import MemoryKind
from short_drama.schemas.base import Identifier, InputModel
from short_drama.schemas.canvas.document.canvas import SourceKey


class ReadTaskResults(InputModel):
    operation_id: Identifier = Field(
        description="本任务 result_sources 中真实成功写入操作的 ID；不是 task_id 或 run_id。"
    )
    ids: Annotated[
        list[SourceKey],
        Field(
            max_length=50,
            description="可选的成果身份列表；每项必须来自该 operation 的 result_refs。",
        ),
    ] = Field(default_factory=list)
    character_offset: Annotated[
        int, Field(ge=0, le=1048576, description="返回成果正文的字符分页偏移，从 0 开始。")
    ] = 0
    character_limit: Annotated[
        int, Field(ge=1, le=6000, description="本次最多读取的正文字符数；较长结果按返回游标继续。")
    ] = 4000


class DescribeTarget(InputModel):
    model_offset: Annotated[
        int, Field(ge=0, le=100000, description="模型能力目录的分页偏移，从 0 开始。")
    ] = 0
    model_limit: Annotated[int, Field(ge=1, le=20, description="本页最多读取的模型配置数量。")] = 10
    result_before_id: Identifier | None = Field(
        default=None, description="成果目录游标；使用上一页返回的 next_before_id，首次读取时省略。"
    )
    result_limit: Annotated[
        int, Field(ge=1, le=20, description="本页最多读取的真实成功操作来源数量。")
    ] = 10


class ReadSelectedSubjects(InputModel):
    ids: Annotated[
        list[str],
        Field(
            max_length=16,
            description="当前任务已选入主体的 subject_key；来自选入快照，不是数据库 ID。",
        ),
    ] = Field(default_factory=list)
    character_offset: Annotated[
        int, Field(ge=0, le=1048576, description="主体正文字符分页偏移；每个主体从 0 开始。")
    ] = 0
    character_limit: Annotated[
        int, Field(ge=1, le=6000, description="本次每段最多返回的正文字符数；按响应 next 继续。")
    ] = 4000


class ReadReceipt(InputModel):
    operation_id: Identifier = Field(
        description="当前 scope 的 operation ID 或明确交接的真实操作回执；不是 task_id 或 run_id。"
    )
    include_result: bool = Field(
        default=False, description="是否分页回读持久保存的原始工具结果正文。"
    )
    character_offset: Annotated[
        int, Field(ge=0, description="原始工具结果正文的字符偏移，从 0 开始。")
    ] = 0
    character_limit: Annotated[
        int, Field(ge=1, le=6000, description="本页最多读取的字符数；按 next_offset 继续回读。")
    ] = 4000


class MemoryCatalog(InputModel):
    offset: Annotated[int, Field(ge=0, le=100000, description="记忆目录分页偏移，从 0 开始。")] = 0
    limit: Annotated[int, Field(ge=1, le=50, description="本页最多返回的记忆条目数。")] = 30


class SearchMemory(InputModel):
    query: Annotated[
        str, Field(min_length=1, max_length=120, description="用于检索本人长期记忆的关键词或短语。")
    ]
    limit: Annotated[
        int, Field(ge=1, le=20, description="最多返回的匹配摘要数；需要正文时再调用 read_memory。")
    ] = 12


class ReadMemory(InputModel):
    ids: Annotated[
        list[Identifier],
        Field(
            min_length=1,
            max_length=8,
            description="要读取的 memory_id；使用目录/搜索结果中的真实身份。",
        ),
    ]
    versions: dict[str, Identifier] = Field(
        default_factory=dict,
        description="memory_id 到 content_version 的映射；使用目录/搜索返回的版本。",
    )
    offset: Annotated[int, Field(ge=0, le=12000, description="长记忆正文分页偏移，从 0 开始。")] = 0
    character_limit: Annotated[
        int, Field(ge=1, le=6000, description="每次最多读取的正文字符数；长内容按返回游标继续。")
    ] = 4000


class ReadHistory(InputModel):
    from_seq: Annotated[
        int, Field(ge=1, description="起始消息 seq，包含该位置；使用会话历史中的消息序号。")
    ] = 1
    to_seq: int | None = Field(
        default=None, ge=1, description="可选结束消息 seq，包含该位置；省略时读取到当前最新消息。"
    )
    limit: Annotated[int, Field(ge=1, le=50, description="最多返回的消息数。")] = 20
    character_offset: Annotated[
        int, Field(ge=0, le=60000, description="合并历史正文的字符分页偏移。")
    ] = 0
    character_limit: Annotated[
        int, Field(ge=1, le=6000, description="本段最多返回的正文字符数。")
    ] = 4000


class ReadResource(InputModel):
    ref_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=240,
            description="当前任务附件/资源目录返回的 ref_id，不是 URI 或存储路径。",
        ),
    ]
    purpose: Annotated[
        str,
        Field(
            min_length=1, max_length=300, description="说明读取资源是为解决当前任务中的什么问题。"
        ),
    ]
    detail: Literal["low", "high", "original"] = Field(
        default="low", description="媒体像素细节级别；文本资源不使用此选项。"
    )
    crop: tuple[int, int, int, int] | None = Field(
        default=None,
        description="可选图像裁剪框 (left, top, right, bottom)，必须有正宽高；按图像坐标。",
    )
    offset: Annotated[
        int, Field(ge=0, le=1048576, description="长文本资源分页字符偏移，从 0 开始。")
    ] = 0
    limit: Annotated[
        int, Field(ge=1, le=6000, description="本次最多读取的文本字符数；媒体读取不按文本分页。")
    ] = 4000


class LoadSkill(InputModel):
    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=120,
            description="search_skills 或用户选中方法返回的真实方法名。",
        ),
    ]
    content_version: Identifier = Field(
        description="所选方法的冻结内容版本；必须与目录结果完全一致。"
    )
    purpose: Annotated[
        str,
        Field(
            min_length=1,
            max_length=240,
            description="说明此方法如何服务当前任务；影响加载范围审查。",
        ),
    ]


class ReleaseSkill(InputModel):
    activation_id: Identifier = Field(
        description="当前 scope 的 Skill 激活 ID；只可释放 source=auto 的方法。"
    )


class RememberMemory(InputModel):
    kind: MemoryKind = Field(
        default="setting", description="记忆分类；按内容选择 setting/preference/fact 等受支持类别。"
    )
    memory_key: Annotated[
        str,
        Field(min_length=1, max_length=120, description="同一记忆主题的稳定键，用于识别重复偏好。"),
    ]
    title: Annotated[str, Field(min_length=1, max_length=200, description="供目录显示的简短标题。")]
    content: Annotated[
        str,
        Field(
            min_length=1, max_length=12000, description="仅保存来源明确支持、适合跨会话复用的正文。"
        ),
    ]
    source_message_ids: Annotated[
        list[Identifier],
        Field(
            min_length=1,
            max_length=8,
            description="本任务中已应用的真实用户消息 ID；不能填写助手消息 ID。",
        ),
    ]


class ChangeMemory(InputModel):
    memory_id: Identifier = Field(description="memory_catalog/search_memory 返回的真实记忆 ID。")
    content_version: Identifier = Field(
        description="该记忆当前版本；冲突后须重新读取，不可复用旧值。"
    )
    content: str | None = Field(
        default=None,
        min_length=1,
        max_length=12000,
        description="更新/恢复后的新正文；遗忘操作写明用户撤回事实。",
    )
    source_message_ids: Annotated[
        list[Identifier],
        Field(
            min_length=1, max_length=8, description="支持此次修改、遗忘或恢复的真实用户消息 ID。"
        ),
    ]


SubjectKey = Annotated[str, Field(pattern=r"^subject:[a-f0-9]{64}$")]
