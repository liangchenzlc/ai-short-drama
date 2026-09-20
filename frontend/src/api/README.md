# 前端 API 约定

## 分层

- `http.ts` 是唯一 Axios 实例：默认 `/api/v1`、15 秒超时，拦截器把网络、HTTP 和服务端错误转为 `ApiError`。不记录请求、响应或密钥，不触发全局 toast，不自动重试写入。
- `types/` 定义服务端原始 DTO，字段保持 snake_case。`id`、`row_version` 均为十进制字符串，不转为 JavaScript number。
- `modules/` 封装资源请求及 DTO → UI 模型映射。组件只调用模块，不直接使用 Axios。
- 页面管理加载、分页、错误及提交状态。列表使用 AbortController 取消过时请求；取消不提示错误。成功写入后重新查询，避免持有默认切换后的旧版本。

## AI 配置

资源路径 `/ai-model-configs` 无尾斜杠。支持 GET 列表（`service_type/offset/limit`）、GET 详情、POST 新建、PATCH 编辑、DELETE 删除以及 PUT `/{id}/default`。

列表响应为 `{items,total,offset,limit}`。PATCH 和 PUT 默认切换传 `{row_version: string, ...}`；DELETE 使用查询参数 `row_version`。发生 409 时不自动重试或覆盖；用户主动重新加载并核对最新内容。新建始终非默认，默认配置在列表单独设置。

每条配置一个 `model_key`。保存名称、提供商、模型标识、服务地址和启用状态；预设仅帮助填写。响应没有密钥，仅有 `has_api_key`。编辑时密钥留空即省略 `apikey`，保留旧值；输入新值表示替换；明确选择清除才发送 `null`。密钥仅在当前表单状态内，不进入 Context、localStorage 或 IndexedDB。

`discoverModels` 调用 `POST /ai-model-configs/discover-models`，使用当前未保存的 `base_url/apikey`，编辑时可传 `config_id` 复用服务器密钥。专用请求超时为 20 秒。更换地址后不能复用旧地址密钥，需要重新输入；无密钥配置不受此限制。响应 `items[].id` 填充可输入下拉框，获取成功自动展开，可直接选择；搜索关键词与当前模型值分离，重新展开显示全部选项，键入时按不区分大小写的子串匹配。`truncated` 提示部分列表，仍允许手动输入。获取失败不清除已填写的模型。地址、密钥变化及关闭弹窗会取消旧请求；旧响应不能重新填入建议。下拉层必须挂载在原生 dialog 内，不能使用 document.body 默认容器。

错误响应为 `{error:{code,message,fields?:[{field,message}]}}`。请求层依据 HTTP 状态生成固定中文错误，保留规范 code，并将字段映射为安全中文标签，避免回显服务端输入。页面根据业务场景展示错误，保留失败表单。网络中断或超时时，写入是否成功可能未知，应刷新列表确认后再决定是否重试。

## 环境和部署

复制 `.env.example` 到 `.env.local` 可覆盖开发设置；文件内只放公开配置。`VITE_API_BASE_URL` 默认 `/api/v1`，会打包到浏览器，禁止放入凭据。`API_PROXY_TARGET` 仅供 Vite 开发代理读取，默认 `http://127.0.0.1:8000`。

生产环境在同一域名把 `/api/` 反向代理到后端，其余页面使用 SPA fallback；API 路由必须优先于 fallback。Vite 开发代理不会打包进生产资源。跨域部署需另行配置后端 CORS。模型密钥由服务端管理，不属于前端环境变量。
