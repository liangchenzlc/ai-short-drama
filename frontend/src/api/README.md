# 前端 API 约定

HTTP 公共契约见[接口说明](../../../docs/api/README.md)，目录树见[目录与职责维护](../../../docs/instruction/目录与职责维护.md)。

## 请求与类型

- http.ts 是唯一 Axios 客户端，统一 API 基址、Cookie／CSRF、超时与错误映射；不自动重试写入、不全局弹 toast。
- api 按业务／功能组织请求函数，具名 DTO 放 src/types 对应目录。字段保留 snake_case，ID 和版本保持十进制字符串。
- API 不依赖 features、pages、app；表单转换、状态生命周期与错误文案由业务功能承担。通用组件只接收数据及回调。
- 宿主内部使用 @host/；画布只通过 integrations/canvas 的公开桥接访问宿主。

## 保存与恢复

正文、分镜、素材、声音和成片使用各自编辑会话串行保存。409 保留本机输入；旧响应不能覆盖新输入；请求结果未知时先确认原回执，不能把读到旧基准当成保存成功。会话按账号与稳定资源隔离，页面卸载只解除订阅。恢复稿保留服务端基准、最后输入与未知请求；存在冲突或损坏记录时保留原文供核对和下载。

script 文档与故事设定分别由 api/creation/writing/documents 和 features/creation/writing 管理；正文共享项目 content_version，四项故事设定有独立 row_version。小说／大纲读写与改编草稿用途已删除。选区改写先 flush，冻结 Unicode 码点选区、载荷与幂等键，成功后精确替换并保留撤销边界，见[写作契约](../../../docs/api/writing-workspace.md)。

素材图、分镜图、分镜视频的私人生成草稿在 api/creation/drafts，下一次生成参数不修改作品描述或已采用媒体。生成前等待草稿保存并核对草稿版本、当前来源版本及对象归属。任务／候选采用继续执行版本与来源核对。

## 助手与 Skill

api/assistant/conversations 提供当前项目的会话、任务与事件。global 只是现行路径前缀，不代表跨项目聊天。附件走 /assistant/conversations/{id}/attachments；模型能力在 api/ai-config/assistant，方法包在 api/skills/catalog，类型分别放对应 types 目录。

发送冻结全文、附件、Skill 内容版本、目标、音轨选择及幂等键；未知回执与普通草稿分开持久化。原请求核对不受当前 pending 附件或页面选择影响；明确未受理才能修正后新建请求。旧 V3 resume 控制和新 Harness 普通消息续跑按实际引擎执行。业务事件刷新编辑区域时继续保留脏稿。

Skill 创建受理不明用原键只读查询回执；文件按准确内容版本读取，切换取消旧请求，迟到内容不能覆盖当前文件。具体契约见[Skill API](../../../docs/api/skills.md)和[助手 API](../../../docs/api/assistant.md)。

## 成片、媒体与环境

导出、预览、恢复及重试固定原请求和原键，受理未知不能换参自动重提。同步失败保留编辑和上次状态；媒体只持久化稳定 ID，签名 URL 过期后重读。画布内部契约继续由独立子包负责，宿主集成桥提供会话、DTO、助手与共享图片工具。

.env.local 仅放公开前端设置；VITE_API_BASE_URL 会进入浏览器，不能放凭据。开发代理使用 API_PROXY_TARGET；生产部署先代理 API 再做 SPA fallback，见[运行指南](../../../docs/instruction/开发运行与验证.md#部署与维护)。
