# AI 配置接口与前端接入

用户要求新增、删除、修改、查询 AI 配置接口并使用 Axios 接入已有页面。原话“三个接口”同时列出了四种 CRUD 操作，本实现按完整 CRUD，覆盖 text/image/video 三类配置。

- HTTP 前缀 /api/v1/ai-model-configs。
- GET /：查询参数 service_type（可选 text/image/video）、offset>=0、1<=limit<=100；返回 items/total/offset/limit。
- POST /：AIModelConfigCreate，返回 201 + AIModelConfigRead。
- GET /{id}：详情，不存在或软删除返回 404。
- PATCH /{id}：AIModelConfigUpdate，必须携带 row_version；成功返回更新记录，版本冲突409。
- DELETE /{id}?row_version=N：软删除，成功204；无请求体。
- PUT /{id}/default：JSON {row_version:N}，原子切换该类别默认配置，返回最新记录。
- 以上列表前缀空字符串路由，无尾斜杠；ID 严格十进制字符串路径，响应保持字符串。row_version 在 JSON 中也使用十进制字符串，前端不计算、原样回传，避免 BIGINT 精度问题；Python 服务内部仍为整数，输入兼容原有整数调用。
- 响应不包含密钥明文或密文，只有 has_api_key。输入验证错误使用 error.code/message/fields 且不回显原始 input（尤其密钥），维持真实HTTP状态码。
- 后端路由调用已有 AIModelConfigService；在依赖中注入 Settings 的加密配置，不把事务或 SQL 放进路由。
- 页面采用真实服务器分页，三类标签切换重置分页，取消过时请求；保存后刷新服务器数据。正在提交时禁止重复操作，失败保留表单；409提示重新加载，不自动覆盖。
- 对齐单配置单model_key表结构：删除不持久化的模型列表/protocol/endpoint/queryEndpoint字段，保留预设厂商帮助填写provider/base_url/model_key。新增默认false；默认配置在列表单独操作，避免创建成功、设默认失败导致重复创建。表单提供启用状态和密钥替换/明确清除动作。
- Axios 单实例位于 api/http.ts，baseURL=/api/v1（环境覆盖）、timeout=15000，统一标准化错误但不弹全局toast、不日志输出请求体。api/modules/ai-model-configs.ts 为唯一业务请求入口；API DTO 与 UI 模型明确映射。
- Vite /api 代理到127.0.0.1:8000；生产部署通过反向代理同源/api。前端环境变量不含数据库/MinIO/API Key秘密。
- 不扩展模型供应商连接测试或AI调用；页面“保存成功”只表示配置入库。

## 验证

真实MySQL临时库的HTTP CRUD/筛选/分页/默认切换/版本冲突/密钥不回显；前端typecheck/build；浏览器走Vite→Axios→FastAPI→MySQL验证新增三类、编辑、刷新仍保留、默认、删除和失败恢复。浏览器测试优先使用独立临时数据库和单独后端端口，前端通过代理测试；生产云库不被测试写入。
