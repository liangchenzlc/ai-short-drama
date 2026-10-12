# 无印刷红色纸盒的 Agnes 真实视频验收

接续[正式视频p/q](2026-10-10-beeftv-agnes-generation-broker-audit.md)，沿用户授权的
[Agnes 免费模型](https://wiki.agnes-ai.cn/zh-Hans/docs/overview)
`agnes-video-2.5-flash`，新增明确无印刷、无丝带的红色纸盒输入。r390
**1 passed / 0 skipped，613.22s，exit0**，全部原UI及Python后置通过；四时间点
原帧、近结尾原尺寸帧和390fresh截图已实际查看，该新输入的内容检查通过。
旧p盒盖/丝带文字的内容失败、q队列拒绝继续保留，不将r称为旧p/q任务恢复或
同一输入修复，不保证模型任意提示词都遵循无字要求。完整M0–M5和goal仍active。

## 独立输入与代码范围

只新增[独立 Python 用例](../../backend/tests/integration/test_canvas_agnes_video_content_broker_live.py)，
复用冻结的[正式生成验收](../../backend/tests/integration/test_canvas_agnes_broker_live.py)
及[原 UI 驱动](../../frontend/canvas/scripts/verify-agnes-live-python.mjs)。
用例通过pytest monkeypatch临时提供视频提示词，结束时恢复原PROMPTS，直接调用
既有正式验收函数；没有修改其文件、浏览器步骤、原输出/权限断言或生产提示词。
输入经原文本框填写、来源保存和原按钮提交，未通过API种成功结果。

新提示词正面描述单个哑光红色带盖纸盒、空白表面、白色无缝摄影棚、完整可见和
缓慢原地转动，明确不含丝带、蝴蝶结、零售包装、印刷、文字或其他物件。该输入
与旧礼盒/丝带示例不同，最终仍按无文字、无标识、红盒保持及可见转动检查。
完整输入保存在上述用例常量及本批冻结请求，不重复写入所有维护文档。

没有生产代码、HTTP、DDL、依赖、前端、标准模式或源适配账本变化。旧31份源码
保持，本片一份独立冻结后合计32。共用助手仍纯对话，本片零工具执行。
初稿Ruff曾报导入pytestmark未使用，改为显式导出后全仓Ruff通过；修改发生在
真实调用前，未删除integration或四个显式opt-in门禁。

## 真实执行和媒体后置

正常API lifespan、独立Scheduler及六类Celery Worker、原任务执行器/Publisher/
Recovery和真实RabbitMQ实际运行，八角色节点980–987互异，fixture1022。
使用独占随机MySQL和随机MinIO桶，凭据沿既有加密信封在服务端内存解密并通过
保存模型API加密保存，浏览器及文档不持有明文供应商密钥。

原390界面保存来源，创建唯一任务，running仅cookies、空origins恢复同一任务后
才释放真实请求。`POST /v1/videos`为text、4秒、720P、16:9，真实200受理；18次
`GET /agnesapi`最终completed，下载1份原MP4，应用终态succeeded。submit观测
记录为submitted，与最终供应商GET和应用终态分开核对。全程只有1次供应商POST，
20个正常AMQP动作＋2个终态原版本/旧版本探针，共22条完成SUCCESS回执，探针
没有再次调用供应商。

原consumer回填稳定资源，终态关闭窗口后再次仅cookies恢复作品，并由原播放器
实际播放。1任务/1调用记录/1绑定/1已附着结果、供应商原字节、MySQL记录、MinIO
定位/文件、浏览器摘要、作者私人任务/模型、成员共享作品及外人404等原Python
后置全部执行。页面与HTTP错误均0；日志、请求/响应和报告检查无明文凭据。

| 项目 | 原供应商产物 |
| --- | --- |
| 格式 | MP4；H.264视频/AAC音轨 |
| 尺寸与帧率 | 1280×720、24fps |
| 时长 | 容器4.48s，视频4.458333s |
| 原字节数 | 930918 |
| SHA-256 | `33693c08321595a03ffe9e0d4a1eef674ae2276439329c849a9e28b3280f5f41` |

`archived.mp4`与`supplier-media-1.bin`逐字节相等，未裁切、覆盖标识或转码来通过
内容检查。AAC存在不代表音频模型或听觉质量已验收。

## 实际内容检查及限制

已实际查看0、0.8、2.2、4.3秒的原片解码帧、四帧合成图、4.3秒1280原帧和390
恢复播放截图：白色桌面与背景中只有一个哑光红色带盖纸盒，采样画面中主体完整可见，
盒体朝向随时间改变，表面空白，没有可见文字、字母、标识、丝带、蝴蝶结、人物
或其他物件。本片通过该输入的可见主体、背景和无字内容检查。
它是实际查看采样帧与原界面的人工检查，不是自动OCR、逐帧质量检测、精确旋转
角度/速度或相机位置测量，也不是所有供应商输出质量保证。
桌面旧j和窄屏新r分别通过各自输入的技术/内容范围，不能把两者称为同一提示词
两宽度的画面一致性证明。原p内容失败及源完整成对视觉门禁没有关闭。

## 运行、清理与接续

在 `backend/` 设置四个既有live/MinIO/browser/broker opt-in、
`SNOWFLAKE_WORKER_ID=1022`及现有Chrome路径，清除助手生命周期观测模式，
使用新的log/basetemp。实际由既有独占MySQL包装调用项目集成入口：

```powershell
uv run python scripts/run_integration.py `
  tests/integration/test_canvas_agnes_video_content_broker_live.py -q --tb=short `
  -p no:cacheprovider --basetemp=.runtime/agnes-video-content-broker-new-temp
```

正式执行为原`.venv`＋`../.runtime/beeftv-resume-run.py`包装，没有直接pytest跳过
数据库。本批日志/acceptance/原MP4/浏览器图与`agnes-video-content-broker-r-review/`、
清理/环境/源码冻结报告均在忽略的`backend/.runtime/`，不提交截图、日志或凭据。
全仓Ruff check及新文件format通过；全仓format实际为765份已格式化、旧迁移脚本
第16/23行混合换行失败，摘要与本片初稿基线相同，未顺手修改。没有前端改动，
旧快测、Node整组、统一build和完整源像素门禁未重复；Starlette既有弃用warning保留。

11队列/2交换机被动确认不存在，10角色/执行PID无存活，4193/API64352无listener，
任务容器/测试桶0，原3桶与用户两服务启动时间保持，两源树干净。一源码＋六
文档变化；2851文件基线含本片调用前初稿，旧31源码保持，新一份冻结后32。
正式视频390已有新无印刷输入成功示例，已成功j/r/质感/角度/打光/HEVC不重复。
继续表情等独立工具、原p内容失败/unknown、同版部署、音频/企业、完整视觉和
[其余M0–M5](2026-10-08-beeftv-remaining-work.md)，R23候选仍未批准或应用，goal active。
