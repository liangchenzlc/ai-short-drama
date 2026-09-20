# MinIO 基础设施

用户要求连接现有云 MinIO（9000 端口），复用 image/video bucket。凭据只存本地忽略提交的 backend/.env。直接实现已请求的基础设施，采用 minio 官方同步 Python SDK；相比通用 boto3，依赖和接口更贴合本次 MinIO 对象操作。

## 边界

- 在 storage/ 封装 SDK、连接池、超时、资源释放和错误映射；service/StorageService 处理媒体种类、雪花对象名、稳定定位值。api 通过依赖注入调用 service。
- 环境配置包括 endpoint（host:port）、access_key/secret_key（SecretStr）、secure（默认 false，对应所给 HTTP 端口）、region（默认 us-east-1）、image/video bucket、连接/读取超时和签名有效期。
- 不自动创建 bucket，不修改 bucket 策略，不启动本地 MinIO，不修改数据库或现有对象。
- 普通上传按 MIME 的 image/video 类别选择 bucket。对象名为 UTC 日期路径 + 雪花 ID + 已知 MIME 扩展名；不把原始文件名作为路径。只接受流及非负已知长度，视频不整体载入内存。
- 返回 bucket/object_name/storage_locator/size/content_type/etag/version_id。storage_locator 采用 minio://bucket/object，可持久化到 media_files.storage_locator；临时签名 URL 不入库。
- locator 只接受配置的两个 bucket，拒绝空 key、点路径段、反斜线、query/fragment 及不合法转义。编码统一 roundtrip，长度满足现有 VARCHAR(700)。
- 客户端提供 bucket 检查、put/stat/get/remove、presigned_get。下载是 context manager，退出始终 close/release_conn。
- 网络、认证、服务端错误映射成不含凭据、对象内容或上游错误详情的 StorageUnavailable；对象不存在映射为 NotFound。
- 客户端在 FastAPI lifespan 创建并复用，退出清理连接池；应用启动不进行远端探活，因此 MinIO 不可用不阻止基础 liveness。
- GET /api/v1/test/minio 只读检查两个 bucket，成功 200，缺配置/不可用/缺 bucket 503。
- 首期只提供 Python 存储服务和探活 HTTP，不添加业务上传路由、数据库联动或文件回收清理流程。

## 验收

单元测试验证 bucket 选择、雪花命名、locator 边界、签名有效期、错误映射和下载资源释放。API 测试验证 liveness 和故障响应。独立 opt-in MinIO 集成测试在指定云端上传本次生成的临时图片及视频对象，检查 metadata、下载内容与预签名 URL，finally 删除这些对象并确认不存在；不创建 bucket。保留已有后端测试。
