# 模型适配器实施与验证记录

日期：2026-09-20。范围：实施计划 Task 2；不涉及任务调度、数据库迁移或 MinIO 持久化。

## 接口

`short_drama.ai` 导出：

- `GenerationGateway(settings).submit(snapshot, request_data, credential, adapter=None)`。
- `GenerationGateway.poll(snapshot, provider_task_id, credential, adapter)`。
- `GenerationGateway.validate(snapshot, request_data, adapter=None)`：纯校验，返回实际协议参数，无 HTTP/DNS。
- `GenerationGateway.download_media(url, max_bytes)`：返回 `(bytes, mime)`。
- `validate_request(snapshot, request_data, adapter=None)`：返回 `{adapter, resolved_parameters}`，允许入队前仅提供媒体 ID；不会修改输入。
- `select_adapter(snapshot)`、`capabilities(snapshot)`、`capability_fingerprint(snapshot, credential_identity)`。
- `GenerationResult`：规定结果字段及 `resolved_parameters`。`GenerationError`：`code/accepted_unknown/retryable/protocol_mismatch`，可兼容内部第二 message 参数，但公开异常文字只来自固定 code。

调用方在 POST 前保存 adapter 和 `validate` 返回的 `resolved_parameters`。执行器把参考媒体 ID 解析为 `input.reference_urls/first_frame_url/last_frame_url` 后提交。适配器不操作数据库或对象存储。

缓存格式 `{adapter, fingerprint}`；快照同时保存 `credential_identity`（原配置密文字段的 SHA-256，不是明文密钥）。指纹涵盖规范化地址、模型、服务类型、凭据身份和解析器版本；完全匹配且类型一致才优先采用缓存。缓存不提供用户可选协议。

## 已实现协议

| 固定适配器 | 请求及结果 |
|---|---|
| `openai_chat.v1` | Chat Completions；兼容 DeepSeek、方舟/百炼兼容文本、GPT 和兼容中转。保留文本、usage、finish_reason，映射 max_output_tokens；GPT 推理系列映射 max_completion_tokens。 |
| `openai_responses.v1` | 明确 `/responses` 地址或已验证缓存；提取 output_text，保留未完整输出原因；可查询已知 response ID。 |
| `openai_images.v1` | Images generations；支持 URL/base64，明确 count/size；不实现 multipart edits，参考图会在入队前被拒绝。 |
| `ark_images.v1` | 方舟 images/generations；参考图、size、顺序多图上限；输出 URL。 |
| `ark_video.v1` | 方舟 contents/generations/tasks 的 POST/GET；首尾帧、ratio/resolution/duration；毫秒必须整秒，绝不静默四舍五入。 |
| `dashscope_images.v1` | 旧版 text2image 异步及 Qwen/Wan 现代 multimodal-generation；Wan2.6-t2i 文生图，Wan2.6-image 参考图编辑；通用 tasks GET。 |
| `dashscope_video.v1` | video-generation/video-synthesis 异步及 tasks GET；文生视频 size、图生视频 img_url/resolution、kf2v 首尾帧。 |

视频未知协议直接拒绝。未支持字段直接拒绝，不丢弃。Ark 顺序多图参数是上限；实际少于 count 的情况交给执行器记录部分结果。能力返回表示本地适配器支持的输入，不代表模型资格或真实厂商连通性。

已核对的百炼型号差异：Wan2.6-image 非流式编辑必须 1–4 张参考图，支持 1K/2K；Wan2.6-t2i 不接受参考图；Qwen 文生图与 edit 输入分开；Wan2.2-i2v-plus 支持 480P/1080P，不接受 720P。图生视频画幅由首帧决定，额外 aspect 明确拒绝。Wan2.6-image 的流式图文混排不在本次范围。

## 安全与失败语义

- 外部 POST 不自动重试，也不内部切换协议。普通 404/405 不能证明协议不匹配，返回 `provider_endpoint` 且 `protocol_mismatch=False`，不授权第二次付费请求。
- 提交后超时、连接中断、5xx、无法解析响应标记受理未知；明确连接未建立可报告未受理。查询 UNKNOWN 不包装成确定失败。
- 除精确主机白名单外拒绝所有非公网解析结果。连接固定到已校验 IP；HTTPS 校验原域名及证书，Host 保留原域名；POST 使用单个地址，GET 允许已验证地址回退。白名单优先 `generation_allowed_hosts`，缺省回退既有 `model_discovery_allowed_hosts`。
- 模型请求禁止跳转。媒体下载不接受鉴权参数、不转发模型凭据；每次跳转重新校验地址/DNS，最多 3 次跳转，禁止 HTTPS 降级，保留签名 query。
- 正文默认 8 MiB；内联图片响应另按最多 4 张、每张 50 MiB 的 base64 开销限定。下载调用者提供硬字节上限，不超过 1 GiB；下载默认 60 秒。输出 manifest 最多 4 项。
- 不返回上游原始错误正文。正文和 usage 中的原始密钥回显被替换；带密钥的结果 URL/任务 ID 被拒绝。

## 验证证据

先运行新增测试，确认 `short_drama.ai` 不存在导致失败，再实现。新增 admission、缓存、UNKNOWN 状态、非法任务 ID、百炼型号限制、manifest 限制亦分别见到失败后修复。

2026-09-20：

```text
backend/.venv/Scripts/python.exe -m pytest backend/tests/unit/test_generation_adapters.py -q
48 passed

backend/.venv/Scripts/python.exe -m ruff check backend/src/short_drama/ai backend/tests/unit/test_generation_adapters.py
All checks passed!
```

协议契约使用真实本地 HTTP 服务，TLS/DNS 固定连接使用外部连接边界替身。包含 Chat/Responses/Images、Ark 图片/视频、DashScope 图片/视频、超时、断线、5xx、非法 JSON、错误字段、密钥回显、私网拒绝、跳转、签名 query、字节上限、纯校验和缓存失效。没有向真实模型发送请求，没有产生模型调用费用，不能称为真实厂商联调。

## 官方资料与限制

已读取官方正文并据此校对：

- https://help.aliyun.com/zh/model-studio/text-to-image-v2-api-reference
- https://help.aliyun.com/zh/model-studio/text-to-video-api-reference
- https://help.aliyun.com/zh/model-studio/image-to-video-api-reference
- https://help.aliyun.com/zh/model-studio/wan-image-generation-api-reference

OpenAI `developers.openai.com` 和 `platform.openai.com` 的 API/图片生成参考页在本次环境返回 403；方舟文档返回动态页面壳。相关实现按稳定协议契约验证，最新 GPT-Image-2 专属尺寸/编辑能力和实际账户支持未经过官方正文或真实请求验证。显式像素尺寸可保留传递，未经确认的分辨率档位不会猜测；需要后续带可用配置的真实联调补足。
