# 模型列表探测验证

参考 cc-switch 的 [模型列表服务](https://github.com/farion1231/cc-switch/blob/main/src-tauri/src/services/model_fetch.rs) 与 [模型输入组件](https://github.com/farion1231/cc-switch/blob/main/src/components/providers/forms/shared/ModelInputWithFetch.tsx)，在现有 AI 配置流程增加读取模型目录、搜索选择和手动输入回退。未增加数据库表或生成调用。

## 已验证

- 后端 `pytest tests -q -p no:cacheprovider --tb=short`：193 passed、30 skipped。
- 其中新增探测 HTTP 测试 24 项：真实本地 HTTP 服务验证路径、版本段、404 回退、去重排序、错误码、空列表、替代响应格式、部分结果标记、大小限制和密钥不回显。
- 新增密钥服务单测 3 项：使用真实加密器及 Session，在 DAO 查询边界提供记录，验证已存密钥地址绑定、只读行为和无密钥配置地址可变更。这些单测不替代真实 MySQL 验证。
- Ruff 检查和格式检查通过，129 个 Python 文件合规；前端 typecheck 与 build 通过。
- 真实公网 HTTPS：OpenRouter `https://openrouter.ai/api/v1` 返回 445 个模型，无需密钥；完整浏览器 Axios → FastAPI → 上游链路返回 HTTP 200。
- 浏览器验证搜索与点击选择、原生弹窗内弹层、认证错误提示、空列表、手动填写、地址变化取消请求并忽略旧响应、防重复探测、手机端点击选择和整页无水平溢出。
- 截图：`frontend/output/playwright/model-discovery-desktop.png`、`model-discovery-mobile.png`；分步脚本为同目录下 `discovery-*-check.js`。

## 修复

独立审查发现并修复三个问题：无密钥配置被地址绑定误拦截、只尝试首个 DNS 地址、Ant Design 弹层默认在原生 dialog 外。浏览器进一步修复模型输入的标签关联和受控输入 maxLength 警告。异步测试使用稳定按钮定位器并等待 React 状态生效，避免将加载文案变化或渲染时序误报为产品错误。

## 当前外部阻塞

2026-09-18 本轮检查中，云数据库账号创建临时库返回 MySQL 1044；直接访问配置的 `ai_short_drama` 数据库也返回 1044。未修改账号权限、连接凭据或业务数据。30 个外部环境测试跳过，其中包括 MySQL 与未启用的 MinIO 集成测试；新增 `tests/integration/test_discovery_persistence.py` 已编写，但因临时库创建权限失败未能执行。

正常开发服务已更新。未保存配置使用当前输入的地址和密钥探测不依赖数据库；配置列表、保存和复用已存密钥需恢复数据库账号权限后再验证。恢复后可运行：

```powershell
uv run python scripts/run_integration.py tests/integration/test_discovery_persistence.py -q
```

本轮没有保存浏览器中的测试配置，没有执行任何模型生成调用。
