# 前端 API 约定

后端公共契约见[接口说明](../../../docs/api/README.md)。本文件只说明前端请求组织与交互约束。

## 分层与类型

- `http.ts`是统一Axios实例，默认`/api/v1`、15秒超时；将网络、HTTP和业务错误转为`ApiError`，不记录正文/凭据，不自动重试写入，不触发全局toast。
- `types/`和`modules/`中的DTO保留服务端snake_case字段；ID与版本保持十进制字符串，不能转换成JavaScript number。
- `modules/`封装资源请求与必要的UI映射；组件不另建Axios实例。
- 页面/会话负责加载、分页、错误、提交和过时响应处理。可取消的列表请求使用AbortController，取消不作为业务失败显示。

## 保存、冲突与生成

小说与剧本使用`writing-session.ts`串行保存共享content_version。旧响应不能覆盖新输入；409暂停保存并保留草稿，网络不确定时先读取服务端核实。导航保护等待保存、确认和其他制作编辑；失败后才允许用户明确决定是否放弃。

素材与分镜使用各自版本；排序还需要storyboard_version。生成前先保存来源内容，采用时提交服务器返回的版本、当前媒体ID和上下文hash；不要在前端生成这些比较值。

`generations/attempt.ts`按操作与请求内容保存幂等标识。请求超时后同一请求复用原键，内容改变或明确新建任务使用新键；不要为了“重试”自动触发第二次付费提交。可执行操作以can_cancel/can_resume/can_retry为准。

图片/视频的asset_id、素材本体id、media_id不是同一概念。参考图提交文件media_id；候选采用接口路径使用生成媒体资产asset_id；目标id则由target.type决定。

素材详情提交 `source:{scene:"asset_image",asset_id,row_version}`，`input.prompt` 为本次补充要求，参考图片列表为空。保存回调必须返回服务端的新版本；短操作锁覆盖保存和提交，不能从尚未更新的 React state 读取版本。历史按素材 ID 查询；切换素材时取消旧请求，轮询错误不覆盖已有任务状态。

候选 `generation` 提供来源任务、版本和 `is_stale/stale_reason`。上传或通用图片候选该字段为 null。采用遇到 `stale_source` 时明确确认后提交 `acknowledge_stale_source`，共享确认标志与旧来源确认标志需同时保留，但均不能跳过目标版本冲突。

## 配置与模型目录

配置PATCH/默认切换提交row_version，DELETE用查询参数row_version。成功写入后重新读取，尤其默认切换会影响另一条配置版本。配置冲突需要用户核对，不能自动覆盖。

Key仅在当前表单内使用：编辑留空省略字段、明确清除发送null、新值替换。不得进入Context、localStorage、IndexedDB或前端环境变量。

模型目录请求超时20秒。编辑可传config_id复用同地址的已存密钥；地址变化后需新密钥。旧响应不得覆盖新地址/密钥下的选择，关闭表单取消请求。获取成功不自动保存；truncated提示目录不完整，始终允许手填模型名。原生dialog内下拉层应挂在dialog内。

## 错误与媒体

错误按业务场景就近展示，保留未提交内容；避免把原始上游文本当成安全UI错误。版本冲突可展示当前版本/引用对象，但不能把错误details作为可信业务数据写回。

临时签名URL只用于显示/下载，不作为持久化身份。过期后重新读取媒体详情。上传走FormData字段file，不手工固定multipart boundary；候选上传与确认采用是两次独立操作。

## 环境

`.env.local`仅放公开设置：VITE_API_BASE_URL默认`/api/v1`并会进入浏览器，API_PROXY_TARGET仅供开发代理。生产同域先代理`/api/`再配置SPA fallback；跨域需要服务端额外配置CORS。资源base与深链部署限制见[运行指南](../../../docs/development.md#部署与维护)。
