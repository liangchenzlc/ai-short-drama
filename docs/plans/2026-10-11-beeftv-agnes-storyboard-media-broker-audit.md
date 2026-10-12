# BeefTV 分镜真实媒体验收：Agnes A/B

日期：2026-10-11。整体分镜媒体验收 **not_passed**，完整 R10/M0–M5 **not_passed**，goal active。
本片只记录两个已实际执行的 1440 桌面批次；历史分镜文本结果见
[分镜真实文本验收](2026-10-11-beeftv-agnes-storyboard-broker-audit.md)。

两批使用原分镜 Table 编写三镜，选择第 1、3 镜，保留未选择镜作为对照；原批准入口、
正式八角色、随机临时 MySQL、真实 RabbitMQ/Celery 和任务独占 MinIO。
图片模型为 `agnes-image-2.5-flash`，直接文本视频为 `agnes-video-2.5-flash`；
本片没有角色绑定、首帧视频、音频或企业账号验收。
供应商调用与浏览器流程分别记录，不能用后台成功或局部截图代替完整验收。

## 实际批次与失败

| 批次 | 实际执行 | 已发生的真实调用与结果 | 保留的失败和未执行范围 |
| --- | --- | --- | --- |
| A1440，Python/driver v1 | OS1，1 warning/83.63s，pytest Exit | 两图各 1 POST200、各 1 call、原任务自然 succeeded/READY；两个结果 detached；视频 0 | driver:447 把原新图 size=1:1 与 16:9 比较；图片绑定、恢复、成员及完整 Python 后置未执行 |
| B1440，Python/driver v2 | OS1，1 warning/304.80s，pytest Exit | 两图各 1 POST200，原图片浏览器 stage 完成；两视频各 1 POST，其中一 200 后自然 succeeded，另一 429/failed | driver:466 要求关窗前两个任务都活跃且无成品，但一视频已明确失败；视频浏览器恢复/绑定及完整 Python 成功后置未执行 |

这里的 pytest Exit 不是通过的测试，也不改写为普通 `1 failed` 统计。
A/B 原日志、execution、失败截图及 reconciliation 保留；
v2 修正的是验收源码对源默认图片比例的预期，并设置 video vquality=720，没有修改产品 UI、
schema、DDL 或依赖。v1/v2 三份源文件分别冻结在
`backend/.runtime/agnes-storyboard-media-source-v1/`、`backend/.runtime/agnes-storyboard-media-source-v2/`，
实际 SHA 以各批 execution 与冻结 manifest 为准。

A 原图片 task：`367458184593227776`、`367458191853568000`。
浏览器失败后 reconciliation 在 4.797s 观测原任务自然完成，八角色仍在运行，
active/unknown/read_errors 为空；没有新 POST、retry、resume、bind 或结果注入。

A 关键原堆栈：

    AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:
    '1:1' !== '16:9'
        at runStage (.../verify-agnes-storyboard-media-python.mjs:447:16)
        at async .../verify-agnes-storyboard-media-python.mjs:550:5

B 原图片 task：`367459366652952576`、`367459375276441600`。
图片 stage 的原 Table 取消、两镜批准、自然 running、仅 cookies 恢复、原 consumer 绑定、
媒体展示、完成后仅 cookies 再恢复和重复点击不新增任务已在原浏览器完成。
原报告的两项恢复与重复点击布尔值为 true，bindings/outputs 各 2，
reconciliation 两个图片结果为 attached。完整 Python 后置仍未到达，
不能由这个 stage 推导成员权限、全部 ORM/媒体字节后置或整批验收通过。

B 原成功视频 task：`367459556248076288`，provider ID：
`task_7qqekcWvzzuYc3CBxhIT8O6IC1HwwY58`。
实际 1 POST200 后 6 GET200，最后供应商 poll=succeeded，保存一份原下载。
另一个视频 task `367459568390586368` 实际 1 POST429，
无 provider task ID、无输出、无下载，应用终态 failed/provider_rate_limit，
`canRetry=true`、`canResume=false`。它是明确拒绝，不是 unknown。

B 关键原堆栈：

    AssertionError [ERR_ASSERTION]: 关闭前两任务必须自然活跃且尚无成品
        at runStage (.../verify-agnes-storyboard-media-python.mjs:466:12)
        at async .../verify-agnes-storyboard-media-python.mjs:551:5

B reconciliation 在 179.078s 观测原 accepted 视频自然 succeeded；
成功视频结果 detached，失败视频无结果。active/unknown/read_errors 均为空。
reconciliation 未发新 POST、retry、resume 或 bind；后台终态不改写 driver:466 的原失败。

## 原媒体与有限内容审查

root 实际看过 A 两张原图片、B 两张原图片、B 五个视频时间点和 B 五张原浏览器 PNG。
本节引用 root 的内容评审；独立清理代理核对原字节和 SHA，没有再次声称自己完成视觉内容认证。

| 原媒体 | 实际可见内容 | 有限结论 |
| --- | --- | --- |
| A/B 红盒图片 | 白色台面、单一红色方形礼盒和红色丝带；未见人物、印刷文字或额外物品 | 可见内容 passed |
| A/B 蓝色容器图片 | 白色台面、单一蓝色圆形敞口陶瓷容器；未见人物、文字或额外物品 | 杯与碗的精确造型语义不认证，partial |
| B 成功视频 | 红色长方形包装盒有可见相对角度变化；盒面明显金色标识、丝带重复白色字样，后方为深色帘幕 | 五采样点的转动有限 passed；禁止文字/标识内容 failed，整体视频内容 failed |

内容审查保存在
`backend/.runtime/agnes-storyboard-media-content-review-a1440.json`、
`backend/.runtime/agnes-storyboard-media-content-review-b1440.json`。
B 五张实看浏览器原 PNG 是 source-table-authored、image-running-restored、image-completed、
image-fresh-terminal、failure；不扩大为全部源像素、完整动效或每个隐藏列可读性认证。

root 后续真实媒体检查 OS0，以配置解析出的 ffprobe/FFmpeg 解码和抽帧；
第一次因 tests 导入路径错误未执行，修正路径后才实际成功。
[原视频检查 manifest](../../backend/.runtime/agnes-storyboard-media-review-b1440-v1/manifest.json)
记录成功视频 1280×720、4.458333s、508994 字节，SHA256：
`3a370da0a3498db8335f24cc0d815177d122cf205c574637139022fc5714a66d`。
原请求 seconds=4，不把测得时长改写成恰好 4 秒。五采样点为
0、1.11458325、2.2291665、3.34374975、4.358333 秒，帧 SHA 以 manifest 为准；
五帧不能证明每一帧、音频或全部动作。

独立审计实际解码四张原 PNG，均 1024×1024，核对两批供应商文件字节/SHA。
B 独立审计自身的 ffprobe 明确 `not_run_executable_not_in_PATH`，
root 后续实际解码是独立的补充证据，不覆盖这个原标记。

## 独立清理与证据保全

A 检查了 8 角色加 image/agent 执行，共 10 PID；B 检查了 8 角色加
image/video/agent 执行，共 **11 PID**，不能写成 10。
各批原 PID 退出，各自专用 API/MySQL 端口与独占容器不存在；
各随机命名空间 11 queue/2 exchange 均以 passive 声明确认缺失，不是只看 cleanup 布尔值。

A 独占 MySQL 容器为 `beeftv-resume-mysql-test-64b6fac8f87243919cfacc9e6dae412b`，
API 55090、MySQL 55004；B 为 `beeftv-resume-mysql-test-25018a3f1ddd447c9b53cda8511cc164`，
API 50471、MySQL 50385，浏览器共用隔离端口 4198。
A v1 现场全局检查时 B 已启动，4198 有新 listener、MinIO 有 3 临时桶，因此保留
cleanup_audit_status=failed。A v2 再查 A 原 PID/端口已退出，
现场 API 50471 归属 B 的 API 父子进程；旧 4198 listener 已退出，旧祖先无法重建。
A 具体桶名未留存，v2 保持 partial：fixture finally 明确删除对象/桶、A 原日志无 teardown 异常，
但不能独立按名证明 A 桶当时已缺失，更不能猜并删除并发桶。

B 结束后的新时间点独立检查通过：11 原 PID、4198/50385/50471 listener、独占容器均 0，
全部 `canvas-test-*` 桶 0，MinIO 总 3 桶。
这是新的全临时桶缺失证据，不覆盖 A v1/v2 的历史观察和缺失桶名限制。

两批原用户服务 PID 28808/28840 的 start_utc 严格保持
2026-10-08T02:00:14.4573905Z / 2026-10-08T02:07:23.8222398Z。
两源 HEAD/clean 均保持：原源 `ef65732bff5ee99019664ca8689608b1c7831cbb`，
固定视觉源 `4ca2a65a7780a8dfcaaa86c33679f84fb04e055c`。

独立审计是
`backend/.runtime/agnes-storyboard-media-a1440-independent-audit-v1.json`、
`agnes-storyboard-media-a1440-independent-audit-v2.json`、
`agnes-storyboard-media-b1440-independent-audit-v1.json`。
A 重核 25 项原工件、B 核对 32 项原工件，各批三份冻结源匹配 execution SHA；
B 清理 passed 与批次 acceptance failed_preserved 分开记录。
root 另执行只读检查 `.runtime/check-storyboard-media-ab-v1.py`，实际 OS0，
生成 `backend/.runtime/agnes-storyboard-media-ab-independent-v1.json`，核对原错误行、
每 task 一次 POST、自然终态/attachment、冻结源码、PNG、视频元数据及五帧 SHA；原失败不改判。
四份历史维护文档在追加前逐字节备份至
`.runtime/agnes-storyboard-media-evidence/ab-doc-before-v1/`；
追加后逐一验证 startswith 原字节，旧正文、旧结论和旧工件不改写。

## 实际验证命令与限制

历史 A/B 真实入口在 backend 工作目录执行：

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File ..\.runtime\run-agnes-storyboard-media-a.ps1
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File ..\.runtime\run-agnes-storyboard-media-b.ps1

入口显式拒绝覆盖既有日志、basetemp、execution 和冻结目录；
上面是历史复现入口记录，不能重跑覆盖 A/B。首次脚本策略拒载未执行供应商，不计真实调用。

root 已实际执行 v2 两个新增 Python 测试/observer 的局部 Ruff check/format 与 driver node --check，均通过。
root 还实际执行以下全范围命令；本片没有另行落盘日志，记录的是 root 工具结果，
不能声称本维护代理独立读取了这些不存在的日志：

    # backend/
    .\.venv\Scripts\ruff.exe check src tests scripts
    .\.venv\Scripts\ruff.exe format --check src tests scripts
    # frontend/canvas/
    npm.cmd run source:check

全 Ruff check OS0；全 format OS1，旧 `scripts/apply_assembly_migration.py` 第 16/22 行
相邻字符串混合 CRLF/LF 缺口保持，输出 1 would reformat / 772 already formatted。
未修复无关迁移脚本，也不写成全 format 通过。
source:check OS0，commit=4ca2a65a7780a8dfcaaa86c33679f84fb04e055c，
files=832、adaptations=206、removed=91；这些是源检查计数，不是测试用例数。
第一次 npm.ps1 被策略拒载，没有运行 source:check；改 npm.cmd 后才实际通过。

## 免费模型请求速率与后续范围

[官方 TokenPlan](https://wiki.agnes-ai.cn/zh-Hans/docs/tokenplan) 第 6 节列免费默认视频 1 RPM，
第 8 节说明同类型 key 共用额度池，多 key 不增加限额。
[官方 FAQ](https://wiki.agnes-ai.cn/zh-Hans/docs/faqs) 和
[视频 Flash](https://wiki.agnes-ai.cn/zh-Hans/docs/agnes-video-25-flash) 一并作为公开协议参考。
上述由协作代理通过 agent-reach 公开只读获取；不是供应商鉴权后测得的账号配额。
免费仍受 RPM 约束，1 RPM 是每分钟请求速率，不是活动视频任务并发上限；
当前账号类型未知，不臆造本账号精确限额或文字模型限额。

v3 正在调整几何形状输入，并准备在原 UI 依据服务端 `canRetry` 对明确限流失败任务显式重试；
当前准备的是独立 `verify-agnes-storyboard-media-python-v3.mjs` 与
`test_canvas_agnes_storyboard_media_broker_live_v3.py`，原三份源码保持 v2。
尚未静态收口或真实执行，不能提前记为 passed、最终冻结，也不改写 A/B 失败。
后续批次必须另建任务独占前缀和冻结源，保留所有原失败和许可边界；
有 provider ID 的 active/unknown 原任务不能盲目重新 POST。

角色版本、可信绑定及首帧 HTTPS 接入仍只有已保存的只读方案，未实施代码、DDL 或真实验收；
音频、收费和企业授权继续暂缓。批量媒体内容、两视频完整原 UI 流程、来源/权限/完整 ORM 后置、
完整视觉/生命周期/性能及同版用户部署仍开放，整体 R10/M0–M5 not_passed，goal active。


## 2026-10-11 接续：C1440 几何输入与两视频明确未受理

C1440 使用独立 v3 测试与 driver、新几何输入，不是 A/B 商业物体的同提示词复测。
实际 OS1、pytest Exit、1 warning/127.35s，不能写成 1 passed；
原失败在 `verify-agnes-storyboard-media-python-v3.mjs:608`：

    AssertionError [ERR_ASSERTION]: 关闭前至少一个原任务必须自然活跃且尚无成品
        at runStage (.../verify-agnes-storyboard-media-python-v3.mjs:608)

原 Table 三镜仍为 4/6/5 秒，选第 1、3 镜，未选第二镜保持。
两个原图片 task `367465218512338944`、`367465225961422848` 各一次真实 POST200，
自然 succeeded/READY，图片结果 attached，在原画布完成与新会话终态可见。

两个原视频 task `367465411974610944`、`367465419989925888` 各一次真实 POST，
分别明确未受理为 503/provider_queue_full 与 429/provider_rate_limit。
两者无 provider ID、accepted_unknown=false、无视频输出/下载；
canRetry=true、canResume=false，不是未知受理。原浏览器在关窗断言之前已观察两者失败，
所以没有自然 active 视频，也没有执行原 UI 显式 retry 或视频恢复。
reconciliation elapsed=0.0，active/unknown/read_errors 空，没有新 POST、retry、resume 或 bind；
完整成功 Python 后置未执行，C 原红保持。

root 实际看过两张 1024×1024 原 PNG 与六张原浏览器 PNG。
[原 C 内容复核](../../backend/.runtime/agnes-storyboard-media-content-review-c1440.json)
记录单个红色立方体、单个蓝色实体圆柱的无文字/标识/额外道具几何小场
`passed_limited`；浏览器仅 partial。六张实看图为 source-table-authored、source-authored、
image-running-restored、image-completed、image-fresh-terminal、failure。
这不认证所有隐藏字段、完整源像素/动效、生命周期或性能。
视频输出 0，视频播放、帧及内容认证 not_run；不能把已执行且被拒的两个 POST 写成未调用。
A/B 原失败、蓝杯/碗 partial 和 B 视频文字/标识内容 failed 均不改判。

[独立 C 清理审计](../../backend/.runtime/agnes-storyboard-media-c1440-cleanup-audit-v1.json)
实际通过：8 角色加 image/video/agent 执行共 11 PID 全退出，
4198/50928/64530 无 listener，独占
`beeftv-resume-mysql-test-5b4844` 前缀的完整容器身份以审计为准且确认不存在；
11 queue/2 exchange passive 缺失，MinIO 全 canvas-test 桶 0、总 3 桶。
原用户服务 28808/28840 的 start_utc 和两源 HEAD/clean 保持；
审计核对 31 原工件及三份 v3 冻结源。cleanup passed 与 acceptance failed_preserved 分开。

实际历史入口在 backend/ 为
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File ..\.runtime\run-agnes-storyboard-media-c.ps1`，
入口仍拒绝覆盖 C 原日志、execution 和 basetemp；不要重跑覆盖。
AB 文档交接 QA 的初读时 C 只在准备，完成 QA 时 C 已实际运行失败：
`.runtime/agnes-storyboard-media-evidence/ab-doc-root-handoff-qa-v1.json` 明确记录状态变化，
不伪写当前未启动，也不修改 AB 当时的准备态正文。

本次先备份五份当前文档字节，再仅追加 C；AB 正文、AB 追加和全部旧工件保持。
v4 仅准备中，未记录运行结果。角色/可信版本、首帧 HTTPS、音频/收费/企业、
完整两视频流程、来源/权限/完整 ORM 后置、视觉/生命周期/性能和同版部署仍开放；
整体 R10/M0–M5 not_passed，goal active。


## 2026-10-11 接续：D1440 原 UI 串行重试与部分真实媒体

D 独立执行复用 C 几何输入，使用冻结 v4，不改 A/B/C 原件或失败结论。
实际 OS1、pytest Exit、1 warning/409.10s；不能记录为 1 passed。
原 Table 三镜 4/6/5 秒，选镜 1、3。两张图片 task `367472856646107136`、
`367472866884403200` 各一次真实 POST200，自然 succeeded/READY/attached。
两个原视频 `367473058849308672`、`367473070232649728` 各一次 POST503/provider_queue_full，
无 provider ID、accepted_unknown=false、canRetry=true、canResume=false，明确未受理。

原 UI 每原镜最多一次 retry，严格串行；最新视频 POST 完成至少 65 秒后点击。
镜 1 retry `367473390039941120` 实际 1 POST200 + 6 GET，自然 succeeded/READY/attached；
provider ID `task_FxMTvYBFsHXZ0R7gGgmcVHYhdeIQkEbW`。
首 retry 真正 running、有 provider ID、无成品时完成 cookies-only 关窗恢复；
原 consumer 成功 bound 回执 1。距前一 POST 完成 65009ms 后才点击，实际 wait 57599ms。
镜 3 retry `367474235125088256` 在镜 1 成功并稳定回填后，距镜 1 POST 完成 191914ms 点击，
仍 POST503/provider_queue_full、无 provider ID、accepted_unknown=false，流程立即停止。
没有对 retry 再次 retry/resume。原失败为：

    AssertionError: 该镜唯一 retry 再次失败，立即停止后续点击
        at .../verify-agnes-storyboard-media-python-v4.mjs:606

reconciliation elapsed=0.015s，active/unknown/read_errors 空；
6 tasks、6 records，各 call_no=1，3 个结果 attached（两图、一视频）；
没有新增 POST/retry/resume/bind。完整成功 Python 后置、两视频完成与正式点击播放、
终态恢复、成员媒体/权限、全部旧新任务重复及过期 AMQP 后置均未执行。

[D 内容结论](../../backend/.runtime/agnes-storyboard-media-content-review-d1440.json)
记录 root 实际查看两张原 PNG、九点视频 contact sheet 和全部 12 张原浏览器 PNG。
两图均 1024×1024：红立方体、蓝实体圆柱无可见文字/标识/人物/额外道具，内容小场 passed_limited。
成功视频 608728 bytes、1280×720、4.458333s（原请求 4 秒），
SHA256 `ba21b3f904c7fb019d086881c8a1b281731b7e0288a081e620324a1c1979baad`；
ffprobe 与 FFmpeg 完整视频流解码实际 OS0。九点显示单个红立方体在白色圆桌上转动，
背景竖木条，未见文字/标识/人物/额外桌面物品；提示词未禁止该背景，不新增纯白背景条件。
视频内容仅 passed_limited；九点不是全部帧认证，不认证音频或精确速度。
蓝视频无输出，内容 not_run；浏览器整体 partial，正式视频点击播放检查 not_run。
D 整体 not_passed，B 视频文字标识内容 failed 保持。

[D 清理审计](../../backend/.runtime/agnes-storyboard-media-d1440-cleanup-audit-v1.json)
区分现场独立检查和 root 补充：同伴在当前权限实查 11 exact PID 不存在；
54932/64583/4198 无任何 TCP 匹配行；11 queue/2 exchange passive 确认缺失；
三实际独占桶 list/HEAD 不存在，MinIO 总 3 桶。
11 产品文件、live/archive 六源码、两源 HEAD/clean、原用户服务 start 与 A/B/C 十二旧原件 SHA 保持。
同伴原 temp 读取与 Docker named-pipe 受权限限制；任务/provider/reconciliation 原件细节依据
[root 脱敏投影](../../backend/.runtime/agnes-storyboard-media-d1440-root-evidence-projection-v1.json)，
不能称作同伴独立原 temp 读取。顶层 execution/log 及派生抽帧可读项另行核 SHA。
精确容器 `beeftv-resume-mysql-test-f5684539a0e34c5f84965dda2d09ec13` 不存在由
[root 只读容器补证](../../backend/.runtime/agnes-storyboard-media-d1440-root-container-check-v1.json) 支持，
实际 OS0/exact_match_count=0。清理完成不改验收失败。

历史入口（backend/）为
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File ..\.runtime\run-agnes-storyboard-media-d.ps1`，
入口拒覆盖 D 日志、execution 和 basetemp，不要重跑原前缀。
冻结 Python SHA256 `fe76a6d99dea85a92be9eba241070759798db4a69ebf19fc3eed9e25566d4d88`，
driver `e25da33469fce00065402b6ce94e2b9e61065fa1088fbdd7cc0507ddfa7c9525`，
observer `c0b9e5d8978f189ff54feefd1b38d77bacc5dd0f7eb24060c54c8159060db83d`。
root 局部 Ruff check/format、Python AST、node --check 均实际 OS0；产品没有本轮变化，
不重述旧快测为新执行，旧全 format 缺口保持。

五份文档先逐字节备份再仅追加 D。下一轮 E 入口尚未运行；
首帧 HTTPS 与可信业务项目绑定候选仍隔离实施，未应用产品、未执行 DDL、未真实首帧验收。
角色不可变版本、完整两视频后置、音频/收费/企业、完整视觉/生命周期/性能与同版部署仍开放；
整体 R10/M0–M5 not_passed，goal active。


## 2026-10-11 接续：E1440 队列满、失败停止及 D 计时口径澄清

D 追加中的 E 未运行是当时快照。E 已于 01:05:29–01:08:58 UTC 独立执行，
仍使用冻结 v4，新的 e1440 日志、execution、basetemp 和身份快照不覆盖 D。
v4 内部 title/input_revision/scope 仍含 D 字样，是冻结源码标签；本次执行身份以 E execution 为准，
不能将 E 误并入 D。新两入口由 D 逐项派生，PowerShell Parser 0 error；
[同伴入口与 D 文档 QA](../../.runtime/agnes-storyboard-media-e-entry-and-d-doc-qa-v1.json)
确认只更换前缀/标签、冻结三源码及六 live/archive hash 均不变，五文档旧 prefix 保持。

E 实际 OS1、pytest Exit、1 warning/195.73s，不是 1 passed。
蓝圆柱图片 task `367478060028346368`、红立方体图片 `367478066835701760` 各 1 POST200，
自然 succeeded/READY/attached，图片 running 时与 fresh 终态均恢复可见。
两个原视频 `367478251972280320`、`367478258716721152` 各 1 POST503/provider_queue_full；
镜 1 唯一原 UI retry `367478580063322112` 也 1 POST503/provider_queue_full。
三者无 provider ID、accepted_unknown=false、canRetry=true、canResume=false，无输出或下载，明确未受理。
driver 原失败为：

    AssertionError: 首个重试未能观察到真实 running，停止
        at .../verify-agnes-storyboard-media-python-v4.mjs:456

首 retry 失败立即停止；镜 3 retry 未执行，没有对 retry 再次 retry/resume。
E driver 冷却完成字段 elapsed_since_reference_ms=65016、actual_wait_ms=58877；
随后 clicked_at=01:08:35.495 UTC，与 reference_post_finished_at 的时间戳差为 66.988343s，
两种记录都满足至少 65 秒，不把冷却完成误称 click 时刻。
reconciliation elapsed=0.0s；API/DB 完整覆盖五任务，active/unknown/read_errors 空，
application_terminal_observed 和 natural_terminal_observed 均 true；
5 tasks、5 records 各 call_no=1、两图片结果 attached，无新 POST/retry/resume/bind。
应用自然终态不替代供应商受理成功；本轮视频均供应商拒绝。

[E 内容复核](../../backend/.runtime/agnes-storyboard-media-content-review-e1440.json)
记录 root 实际看过两张原 1024×1024 PNG 和全部九张原浏览器 PNG。
两图完整构图、单个红立方体/蓝实体圆柱、无可见文字/标识/人物/额外道具，仅小场 passed_limited。
蓝图 SHA256 `bebabca5dc488287f07bf8c321ddb3f17f81025f11606814366865ec12542782`、787055 bytes；
红图 `218ab672c3d069ac39d07478625c23755c75ecc0004fea6d56b137808088fdca`、687384 bytes。
retry-started 截图的“当前画布 1 个进行中”是提交后的瞬时应用状态，不能据此声称供应商真实 running。
视频输出 0，视频内容、解码、播放及 running 恢复 not_run；浏览器整体 partial，E 整体 not_passed。
完整成功 Python、视频完成/终态恢复、成员媒体权限、全部旧新任务重复及过期 AMQP 后置未执行。

[E 清理审计](../../backend/.runtime/agnes-storyboard-media-e1440-cleanup-audit-v1.json)
与验收失败分别记录，独立进程/端口/队列/实际桶、原服务及源码检查与可读原件边界以该审计为准。
[root 原件脱敏投影](../../backend/.runtime/agnes-storyboard-media-e1440-root-evidence-projection-v1.json)
和 [root 精确容器补证](../../backend/.runtime/agnes-storyboard-media-e1440-root-container-check-v1.json)
单独注明来源；精确容器 `beeftv-resume-mysql-test-6048d5380d114ed989577441054e8ad3`
实际 OS0、exact_match_count=0。不将同伴无法直接读取的原件或 Docker 状态称为独立直接检查。

D 主文先前“距 POST 完成 65009ms/191914ms 点击”的措辞需要澄清：
这两个值是 driver 的 cooldown.wait_finished_at 时 elapsed_since_reference_ms，随后才 click。
同伴用 clicked_at 与 reference_post_finished_at 原时间戳另算的差为 66.985233s/194.022565s。
它们是不同观察时点，不改 D 的 65 秒边界、严格串行、镜 1 成功回填、镜 3 唯一 retry 失败结论。
D 和全部 A/B/C 原失败及 B 内容 failed 继续保留，不覆写历史原件或当时快照。

五份当前文档再次逐字节备份，再仅追加 E 和上述澄清。历史 E 入口为
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File ..\.runtime\run-agnes-storyboard-media-e.ps1`
（backend/）；拒覆盖已有 E 工件，不要用同前缀重跑。
首帧 HTTPS 修复 v2 和可信业务绑定仍是隔离候选，尚未应用产品、DDL 或真实首帧验收；
本次没有新增产品变化，不重述旧快测为新执行，旧全 format 缺口保持。
角色不可变版本、完整两视频后置、音频/收费/企业、视觉/生命周期/性能及同版部署仍开放；
整体 R10/M0–M5 not_passed，goal active。


## 2026-10-11 接续：首帧 HTTPS 产品应用与后端验证

首帧 v2 已在 Root 核验冻结候选与原字节后应用 15 个产品路径；没有修改实际 `.env`、业务库或用户原服务。
`minio_public_endpoint` 与 `minio_provider_presign_expiry` 必须显式配对，缺省不开放稳定 Agnes 首帧；
独立 HTTPS 签名器使用显式 region 和静态凭据。首次提交在 POST 前事务保存单向引用指纹，
poll/save 不重签或二次提交，原始/解析响应和 archive 前复核签名回显；异常不保存来源签名 URL。

第一次全量快测实际 OS1：7 failed、2455 passed、90 setup errors、1 warning/436.01s。
90 个错误均是默认 pytest 临时目录 WinError5；7 个失败均是两处旧 SimpleNamespace 缺少真实 ORM 的可空 adapter 属性。
原日志完整保留。仅补两处 adapter=None 夹具，并将现有 archive 前拒回显测试参数化空/非空 adapter，
保留旧缓存业务断言，不放宽产品保护；本次总范围为原 15 路径加 1 个既有测试文件。

新的仓库内独占临时目录复验：专项 95 passed；完整 unit/API 2553 passed、1 warning/472.31s、OS0。
完整 Ruff check OS0，本次 Python 文件范围 format check OS0；此前混合换行检查失败日志也保留。
隔离实际 HTTP/MySQL/MinIO 集成 5 passed、1 warning/23.44s、OS0，供应商与公网签名器是替身，
不代表真实首帧或公网 HTTPS 验收。Root 只读补查精确 MySQL 容器/49873 端口不存在、当前 canvas-test 桶为空、
原两服务身份不变；fixture 未保存精确随机桶名，不能把当前命名空间为空说成逐桶独立复查。

D/E 追加中“首帧未应用”是应用前历史快照，以上为当前已应用现态，不覆写旧段落或失败结论。
公网 HTTPS 信息仍待既有问题答复，没有自动外传或隧道；免费分镜媒体 F 仅新入口准备，尚未执行。
可信项目绑定第 1 组仍冻结隔离候选、未应用或执行业务 DDL；角色版本第 2 组仅隔离实施，未 DDL/backfill。
完整 R10/M0–M5 仍 not_passed，goal active；收费、音频、企业验收仍暂缓。
具体命令、原失败、产品 SHA 与清理边界见 [Root 验证记录](../../.runtime/agnes-public-reference-application-v2/product-validation-final-v1.json)。


## 2026-10-11 接续：F1440 视频明确未受理；来源绑定已应用、018 待执行

F 独立复用冻结 v4/C 几何输入，实际 OS1/pytest Exit/1 warning/205.22s。
首次直接执行入口被 PowerShell execution policy 拦截，pytest/模型调用均为 0；原失败收据保留。
随后只在新子进程使用 `-NoProfile -ExecutionPolicy Bypass`，没有修改永久策略。
红图 `367488605334880256`、蓝图 `367488613119508480` 各 1 POST200，自然 succeeded/READY/attached。
原镜 1 视频 `367488804904058880` POST503/provider_queue_full，
原镜 3 `367488812558663680` POST429/provider_rate_limit；
镜 1 唯一原 UI retry `367489158253199360` POST503/provider_queue_full。
三视频各仅 1 POST、无 provider ID/输出、accepted_unknown=false，明确未受理。

原失败堆栈：

    AssertionError: 首个重试未能观察到真实 running，停止
        recoverFirstAcceptedRetry .../verify-agnes-storyboard-media-python-v4.mjs:454/456
        retryOriginalKnownRejections ...:602
        runStage ...:777

镜 3 retry 未执行；没有对首 retry 再 retry/resume。冷却完成 elapsed_since_reference_ms=65003，
实际 click 时间戳距参考 POST 完成 66.819106s。reconciliation 自然终态，API/DB 覆盖五任务，
active/unknown/read_errors 空；5 tasks/5 records 各 call_no=1、两 image attached，无新提交或注入。
[F 内容复核](../../backend/.runtime/agnes-storyboard-media-content-review-f1440.json)保存 Root 实看两原 PNG 和九原截图的结果：
两图均 1024²、单一红立方体/蓝实体圆柱，无可见文字/标识/人物/额外道具，仅小场 passed_limited。
红图 780873 bytes/SHA `59594b8d4a65d93a51a47d6ea8480562cf20aefa8792111e73ae93c4d2373357`；
蓝图 842058 bytes/SHA `95b9680eb0b0a57e894d54822fa92d1ab97fa25ceac40c343f6ed1396b1d03d7`。
image-running-restored 中红图已 completed 但未渲染、蓝仍 submitting；不能写两图供应商 running 已证。
video-retry-started 的“1 个进行中”同样仅瞬时应用状态。视频内容/解码/播放/恢复 not_run，浏览器 partial，F 整体 not_passed。
完整成功 Python、成员媒体权限及全部 duplicate/stale AMQP 后置未执行；A–E 原失败和 B 内容失败保持。

[F 独立清理审计](../../.runtime/agnes-storyboard-media-f-peer-audit-v1/receipt.json)直接实读 43 项原件/冻结源码，SHA/bytes 匹配；
不是只看 Root derived projection。精确容器 `beeftv-resume-mysql-test-ce9c0c0c23dd487984005a9731ea2669`、
11 PID、50155/50264/4198 listener、11 queue/2 exchange 与三个实际桶均不存在。
原两服务启动时刻、两源 HEAD/clean、11 产品 hash 保持。初次审计可选 errors 字段 KeyError OS1 保留，
兼容字段后独立审计 OS0；没有修改原件、产品或执行清理操作。清理通过不改变验收失败。

此前“可信绑定仍候选”是历史快照。来源绑定第 1 组已核验后应用 29 路径，
无锁授权后按项目 ID 排序取得来源/目标/业务及 pending 媒体父锁；ready 回放保持目标权限检查。
自由图、ZIP 和旧图仍 NULL，不从 JSON/owning ID 推断业务项目。
[独立应用审计](../../.runtime/beeftv-source-binding-application-v1/independent-audit-v1.json)确认全部当前/冻结/备份 hash 与文档旧 prefix，
九阶段实际通过：后端专项 42、真实随机 MySQL 25、Node 29/219/422 均 0 skip；完整 Ruff、17 文件 format、
两包 build 与 832 源 hash 检查通过。集成无 MinIO/供应商，可信父关系来自显式测试种子，不证明旧源真实映射。
绑定后完整 backend 回归为 1 failed/2566 passed/1 warning/476.13s，
唯一 `test_sound_production.py:211` 音频混合峰值 0.0，原日志保留，正在独立定位；不能写全量通过。
当前 `short_drama` 018 只读 precheck 仅缺 nullable 列/索引/RESTRICT FK，changed=[]，DDL 尚未执行。
既有 1 canvas/4 character assets/0 project_assets 无可信来源映射。业务迁移待回归边界澄清后显式执行并记录。

五文档逐字节备份后仅追加此段。G 使用新前缀继续免费验收，结论待单独收回；冻结 v4 标准不变。
首帧产品已应用但真实公网 HTTPS 验收未做；角色第 2 组仍隔离候选，未应用/DDL/backfill。
收费、音频供应商及企业验收继续暂缓；同版部署、其他视觉/生命周期/性能与完整 R10/M0–M5 not_passed，goal active。


## 2026-10-11 接续：G1440 队列满；当前业务库 018 已执行

G 新前缀独立执行冻结 v4，实际 OS1/pytest Exit/1 warning/193.28s。
红图 `367492401603624960`、蓝图 `367492408033492992` 各一次 POST200，succeeded/READY/attached。
原视频 `367492589537804288`、`367492600380080128` 与镜 1 唯一原 UI retry `367492914780913664`
均各一次 POST503/provider_queue_full，accepted_unknown=false、无 provider ID/视频输出，明确未受理。
driver:454/456 -> 602 -> 777 首 retry 无真实 running 即停，镜 3 retry 未执行；
实际 click 时间戳距参考 POST 完成 66.944941s。
五任务自然终态、API/DB完整覆盖、active/unknown/read_errors空、两 image attached，无额外提交。
Root 实看两原 1024² PNG 与九原截图，单一红立方体/蓝实体圆柱、无可见字/标识/人物/额外道具，
仅几何小场 passed_limited；恢复截图两图均 completed/未渲染，不证明供应商 running。
浏览器 partial，视频内容/播放/恢复、完整成功 Python 权限及 duplicate/stale AMQP 后置 not_run，G 整体 not_passed。
[G 内容复核](../../backend/.runtime/agnes-storyboard-media-content-review-g1440.json)与
[独立清理审计](../../.runtime/agnes-storyboard-media-g-peer-audit-v1/receipt.json)分别记录；
审计直接读原件、不依赖 Root projection，精确容器/11 PID/3端口/11队列/2交换机/3实际桶不存在，
原服务身份、两源 HEAD/clean、产品和冻结源码 hash 保持。A–F 与 B 内容失败均保留。
G 初版入口宽泛替换误及 Program Files 且漏引号后标签，在执行前逐项核对纠正，
初版未执行/0调用、原字节及 Parser v1 收据保留；v2 精确派生、Parser 0 error、Chrome 路径实查通过。

来源绑定第 1 组已应用。当前 `short_drama`/MySQL 8.4.11 于 02:14:21 UTC 实际执行 018 的三条 DDL，
新增 nullable `source_project_id`、`idx_canvas_source_project`、命名 RESTRICT 外键；
随后独立 verify OS0，ready/missing=[]/changed=[]，没有重复 DDL。
原 1 canvas 的 row_version=30 与 properties 摘要不变，4 character assets/0 project_assets 不变，source 全 NULL；
没有推断旧映射、内容回填、密钥配置或用户服务重启。
[实际 DDL 收据](../../.runtime/beeftv-source-binding-application-v1/business-apply-v1.json)和
[回读](../../.runtime/beeftv-source-binding-application-v1/business-verify-v1.json)保存准确 SQL/前后状态。
绑定后第二次完整 backend 实际 2567 passed/1 warning/462.95s/OS0；
第一次 1 failed/2566 passed/476.13s 原日志保持，不能称音频间歇失声已修复。
独立同输入 PCM 对照发现音乐尾段截断，帧对齐最小候选正在隔离核验，未应用产品。

角色 v1 已冻结但独立审查发现 DML 形态/legacy bulk 两处保护缺口，v2 增加限定 AppSession 防护，仍未应用/019/backfill。
v1 隔离真实 MySQL 首轮 16 passed/1 failed/26.89s；失败是制造 nullable 漂移时被已有 FK 的1832阻止，
未进入产品漂移断言；旧失败保持，v2 将精确修夹具后新前缀复验，不计 v1 为全通过。
本段与数据库迁移说明均仅追加，早先“018 待执行”是历史快照。
真实公网 HTTPS、角色来源/菜单、同版写者部署、其他视觉/生命周期/性能和完整 R10/M0–M5 not_passed，goal active。
收费、音频供应商、企业验收仍暂缓。


## 2026-10-11 接续：音频断尾最小修复已验证；H1440 在图片活动断言停止

绑定后间歇失声已用相同六输入真实定位：冻结原图重放 100 次有 49 次音乐尾段提前消失、7 种 PCM，对白保持；问题已出现在 sidechaincompress 分支。最小两文件修复只为其两路输入统一 1024 样本帧与样本时钟，保留压低、混合、限幅、音量、视频时长和字幕参数；加强原测试的 AAC 窄窗与原混音 WAV 的 96000 样本/末端非零断言。Root 先应用测试，旧产品实际 1 failed/4.03s，`test_sound_production.py:214` 的 1.90–1.95s 峰值为 0；随后精确应用冻结 after 两文件。

实际验证：音频/成片专项 17 passed/58.03s，20 轮加强后的真实本机 FFmpeg 回归全部 OS0；完整 backend unit/API 2567 passed/0 skipped/1 warning/478.42s/OS0，完整 Ruff 和两文件 format OS0。独立审查实读 140 原 WAV，完整候选 20/20 均与旧完整成功参考 PCM 逐字节相同，96000 帧、末端非零到 1.9999375s；原 49/100 缺尾和所有旧失败保持。验证初入口因 `uv` 未在该进程 PATH 中导致 WinError2、未执行测试；本机实际存在 `backend/.tools/uv/uv.exe`，后续沿已有 `.venv` 模块入口完成上述实际检查，未改依赖。对应 [产品应用/验证](../../.runtime/beeftv-binding-sound-regression-v1/product-application-v1/verification-v2/full-receipt.json)、[旧红记录](../../.runtime/beeftv-binding-sound-regression-v1/product-application-v1/before-receipt.json)和[独立 PCM 复核](../../.runtime/beeftv-sound-render-peer-review-v1/receipt.json)。这是本机媒体处理验证，不是暂缓的真实音频模型验收。

H 独占入口由 G v2 精确派生并经独立 Parser/hash QA，冻结 v4/C 几何标准没有修改。首次直接 ps1 调用被本机执行策略拦截，尚未生成身份快照/执行测试/调用模型；随后仅任务子进程使用 ExecutionPolicy Bypass，未改系统策略。H 实际 OS1/pytest Exit/1 warning/87.33s，红图 `367498678534750208` 与蓝图 `367498701783777280` 各一次 POST200，原供应商 PNG 各 1024²。driver:724 在记录活动状态时实际一 succeeded/READY、另一 running/submitting，`active + rejected == 2` 得到 1 !== 2，即停；截图生成中标签不证明两个供应商任务均 running。无 cookies-only 恢复，视频/重试/视频播放与内容、成功 Python 权限及 duplicate/stale AMQP 后置均 not_run，视频零 POST。

原两图随后自然 succeeded/READY，API/DB 任务覆盖完整、active/unknown/read_errors 空，无新增 POST/retry/resume/bind；两 result 均 detached，不能称图片已回填。Root 实看两原 PNG 和全部三原截图，红立方体/蓝实体圆柱、无可见文字/标识/人物/额外道具，几何内容仅 passed_limited、浏览器 partial，H 整体 not_passed。Root 派生投影 v1 因此未执行视频阶段缺 video-worker-report 而失败，原失败/helper 保持；新 v2 在证实无视频提交/供应商报告后明确记录报告未生成，没有制造报告。见 [H 内容边界](../../backend/.runtime/agnes-storyboard-media-content-review-h1440.json)。

[H 独立原件/清理审计](../../.runtime/agnes-storyboard-media-h-peer-audit-v1/receipt.json)实际 OS0，直接实读 24 原件，10 个精确已记录 PID、50135/50250/4199、精确 H 容器、11 queue/2 exchange/3 原桶均不存在；用户原服务开始时间、两源 HEAD/clean、11 相关产品 hash、冻结 v4 live/archive 均保持。Node/Vite 初始 PID 未保存，不能逐 PID 证明；三个精确/限定命令标记当前计数均 0，只支持标记范围。审计初始辅助 PS 读取卡住后终止 OS1 另记，原件成功审计口径不扩大。A–G 和 B 内容失败保持。

角色 v2 已修 DML/legacy bulk 两静态缺口，独立限定 AppSession 审查 62 个无绑定探针通过，未应用。首 v2 影子随机 MySQL 21 failed/35 passed/26 skipped/69.66s：20 个失败是 Root 影子漏复制提示词，1 个是新采用测试未接本地 storage；不是 v2 新增 `_read` 产品回归，before 原本也调用它。v3 只修身份测试的中央 factory 与既有 CopyStorage，所有产品 after 与 v2 相同。Root 新影子复制实际提示词并显式启用来源绑定，实际 1 failed/80 passed/1 skipped/130.61s；唯一失败越过采用进入导入，`ResourceImportService.create:138 -> Session.flush -> json.dumps` 报 `TypeError: Object of type datetime is not JSON serializable`，角色审计快照日期序列化正在修复。唯一跳过为真实存储集成，不是通过；MySQL 与本地模型/存储替身不称真实供应商验收。两个影子容器各自 cleanup OS0，v2 精确额外回查见其 root-cleanup-v2；v3 Root 额外回查尚待执行。

v1/v2/v3 冻结、1832 夹具失败、21/35/26 和 80/1/1 日志全部保留，角色仍未产品应用/019/回填/同版写者部署。实际 018 已执行与旧来源全 NULL/4 character assets/0 project_assets 的记录保持；没有推断角色来源。真实公网首帧、角色目录/菜单、其他视觉/生命周期/32ms 性能和完整 R10/M0–M5 仍 not_passed，goal active；收费、真实音频供应商、企业验收仍暂缓。


## 2026-10-11 接续：角色 v4 已应用及 019 结构完成；I1440 仍未通过

角色版本 v4 已核原件后应用 30 个产品路径（28 个字节变化、2 个未变），共享迁移说明完整保留实际 018 记录。v4 只补导入媒体快照的 `published_at` UTC/六位微秒 ISO 编码与边界测试；目标媒体发布时间仍由目标 Worker 独立生成，不继承来源发布时间。新随机 MySQL 六模块实际 **81 passed / 1 skipped / 1 warning，131.79s，OS0**；唯一跳过是实际 MinIO 导入字节与临时对象清理用例，不代表该项通过。精确容器/端口额外回查已保存。v1–v4 冻结候选和全部旧红记录保留。

产品首次完整 unit/API 为 **3 failed / 2667 passed**，均是旧 schema 契约测试未计延后 `use_alter` FK 或错误要求公开内部 `current_version_id`。只修两个测试，精确核延后 FK 及其唯一顺序，产品 HTTP/DTO 未改变；两模块实际 96 passed。第二轮专项 **219 passed**、完整 **2670 passed / 0 skipped / 1 warning，pytest 531.43s（外层进程 538.53s），OS0**。随后完整 Ruff 因一个集成测试导入排序 OS1；仅重排该导入，归一 AST 相同，完整 Ruff 和本次 28 个 Python 文件 format check 实际 OS0。`verification-v3` 明确引用 v2 的实际 219/2670 结果与新静态结果，没有重跑或伪造测试。见 [产品验证来源](../../.runtime/beeftv-character-version-application-v4/verification-v3/receipt.json)及[独立门禁审查](../../.runtime/beeftv-character-ddl-v4-peer-review-v1/receipt.json)。

2026-10-11 11:30（UTC+8），在当前 `short_drama`/MySQL 8.4.11 实际执行 019 的 **7 条精确结构 DDL**：两张版本表、版本媒体索引、nullable 当前指针、指针索引、非角色 NULL CHECK、命名复合 FK。随后独立 verify OS0/0DDL，`ready/missing=[]/changed=[]`。全部原资产/画布指纹不变，4 character / 0 project_assets / source 全 NULL，当前指针、版本与版本媒体行数均为 0。没有回填、旧映射推导、声音接入或用户原服务重启。见 [真实执行 SQL](../../.runtime/beeftv-character-version-application-v4/business-apply-v1.json)和[独立回读](../../.runtime/beeftv-character-version-application-v4/business-verify-v1.json)。当前建库 SQL 是 101 张表；原 API、scheduler、text、image、video、render、agent 七角色仍是旧进程，数据回填与同版部署另行准备。

I1440 新 baseline 与冻结 v5 实际 **OS1 / pytest Exit / 1 warning，141.20s**。两图 `367512430353137664`、`367512440218140672` 各一次真实 POST200，关窗前实际 GET 两条均 running/no result/0 outputs；原活动与终态 cookies-only 恢复、两原字节及 attached 回填通过。两个视频 `367512650466017280`、`367512659311804416` 各一次 POST503/provider_queue_full，明确未受理，无 provider ID、输出或 retry。driver:813 停在早期持久图 item queued 与随后任务 GET failed 不一致，并非 active 数量不足；原 UI failure PNG 已显示失败。图片路径的完整成功 Python、成员媒体权限及 duplicate/stale AMQP 后置也未执行，不能据浏览器图片切片称整体验收通过。

Root 实看两原 1024² PNG 和全部六原浏览器 PNG，几何内容小场仅 `passed_limited`、浏览器 `partial`，I 整体 `not_passed`。自然 reconciliation 为 4 tasks/4 records/2 attached results，active/unknown/read_errors 空，没有新增 POST/retry/resume/bind。见 [I 内容边界](../../backend/.runtime/agnes-storyboard-media-content-review-i1440.json)与[I 独立原件/清理审计](../../.runtime/agnes-storyboard-media-i-peer-audit-v1/receipt.json)：直接 30 原件，11 已记录 PID、65453/60478/4200、精确容器、11 queues/2 exchanges/3 实际桶均不存在；原 Node 加七后台进程对的 15 个 StartTime、两源 HEAD/clean、11 产品 hash 及 v5 三原/归档源码保持。隔离 Node/Vite 初始 PID 未保存，不由端口/标记扩大证明。Root 与独立审计各自首投影 helper 都误沿 H 假设不存在的 v5 archive manifest，原 OS1 保留，v2 改核真实候选 manifest 与三归档文件，不制造缺失原件。

只读角色目录第 3 组第 1 切片已冻结候选、未应用：39 新 unit/API + 103 相邻版本共 142 passed，Ruff/format/候选 DTO strict tsc 和独立身份审查通过；双项目/素材/媒体权限及 GET 零 DML 保留。这不是 MySQL/MinIO/UI/供应商验收，实际 DTO/docs 和角色菜单仍未接入。稳定图片 resolver 与新 J/v6 验收时序仅隔离准备，尚未运行。角色回填、可信旧来源、同版七角色部署、真实公网首帧、其他视觉/生命周期/32ms 与完整 R10/M0–M5 继续 `not_passed`，goal active；A–I 与 B 内容失败、R23 未批准候选均保留，收费/真实音频供应商/企业验收仍暂缓。


## 2026-10-11 J/v7 原件与角色备份恢复边界

冻结 v6 从未启动、供应商调用为 0；独立审查发现其 `JSON.stringify(identity)` 误拒真实 I ACK/GET 的对象键顺序变化。v7 仅改用 `node:util isDeepStrictEqual`，保留 early 原件、后续拒绝等待规则和全部 321 Python/226 JS 断言；作者 20 与 Root 独立 18 个纯谓词用例实际通过，均不是供应商验收。Root 纯谓词初次缺 `viewportKeys`、第二次缺 `ordinal` 的辅助脚本 OS1 原件也保留，补齐后 OS0。

J/v7 独立入口 UTC 04:02:07.303508–04:05:49.455940，实际 **OS1 / pytest Exit / 1 warning，209.10s**。红图 `367522545806426112`、蓝图 `367522538474782720` 各一次 POST200，均 succeeded/READY/attached；关窗时及终态 cookies-only 恢复、原字节与回填完成。原红视频 `367522758625411072` 一次 POST503 `provider_queue_full`；原蓝视频 `367522765860585472` 一次 POST429 `provider_rate_limit`；冷却后原界面红视频唯一重试 `367523088834576384` 一次 POST503 `provider_queue_full`。三个视频均明确未受理、无供应商 ID/输出。首 retry 无真实 running，driver618 → recoverFirstAcceptedRetry616 → retryOriginalKnownRejections764 → runStage984 停止；镜 3 retry 未执行，视频恢复/播放/内容及完整成功 Python 权限/duplicate/stale AMQP 后置未执行。

拒绝状态观测实际保留 early revision21（items/taskStatus queued），随后 1 轮追加观察得到真实原生 ACK 与 GET revision22（两个原 items/taskStatus failed），没有复用 early ACK 或替换 early 原字段；不将 I 未观测项追认为通过。自然 reconciliation 为 5 tasks/5 records/2 attached results，active/unknown/read_errors 空，无额外 POST/retry/resume/bind。Root 实看两张原 1024² PNG 和全部九张原浏览器 PNG，单一红立方体/蓝实体圆柱、表面空白、未见字/标识/人物/额外道具；仅几何内容小场 `passed_limited`、浏览器 `partial`、整轮 `not_passed`。见[J 内容复核](../../backend/.runtime/agnes-storyboard-media-content-review-j-v7-1440.json)及[J 原执行](../../backend/.runtime/agnes-storyboard-media-broker-live-j-v7-1440-execution.json)。

[J 独立原件/清理审计 v2](../../.runtime/agnes-storyboard-media-j-v7-peer-audit-v2/receipt.json)实际 OS0，直接读取 34 原件且审计后清单/字节不变；11 已记录 PID、4201/59250/63489、精确容器、11 queues/2 exchanges/3 实际桶均不存在，原用户 15 个 PID+StartTime、两源 HEAD/clean、11 产品 hash、v7 live/archive/candidate 和 77 旧输入保持。初始 Node/Vite 精确 PID 未保存，当前命令标记为空不能补造它们的历史身份。独立 v1 日志正则在 206007 字符单行回退卡住，仅停止身份核明的己方审计 helper PID43344，其 wrapper31572 自然退出、OS-1、没有原件清理通过收据；原件/空日志和取消记录保留。v2 改为分行过滤后重新实际完整审计，不能用 v2 绿结果覆盖 v1。

角色 v2 [当前三表备份](../../.runtime/character-v4-rollout-package-v2/execution/character-v4-rollout-20261011-b/Backup-data.json)实际 OS0：assets16 / asset_versions0 / asset_version_media_references0，类型化快照 SHA `9b5ea592157b81ae1b5c5021ad6bc52957aa90bc3a7c2acc896b74d637b26046`；不含媒体字节或凭据。v2 [原恢复失败](../../.runtime/character-v4-rollout-package-v2/execution/character-v4-rollout-20261011-b/BackupVerify-data.json)实际 OS1 `OperationalError`，原 errno 未保存，不能推断为1044；原随机库未由 app 可见性独立排除。[只读权限核验](../../.runtime/character-v4-backupverify-readonly-audit-v1/receipt.json)表明 app 无 global/精确测试库 CREATE/DROP；没有扩权。业务容器 root 凭据用途核验被自动审核拒绝，查询未执行，不能换途径绕过或称原临时库不存在。

经[Root 独立静态/hash 审查](../../.runtime/character-v4-rollout-package-v3-root-review-v1/receipt.json)，复用同一成功快照与两个原收据（不是 v3 重新 Backup），在新的 c 执行目录实际运行 v3 [BackupVerify](../../.runtime/character-v4-rollout-package-v3/execution/character-v4-rollout-20261011-c/BackupVerify-data.json)与[OS 收据](../../.runtime/character-v4-rollout-package-v3/execution/character-v4-rollout-20261011-c/os-execution.json)，均 OS0。己方独占 MySQL 8.4 Docker 容器、loopback63462，UTC 04:19:18.660192–04:19:28.561915；三表值/类型逐行回读一致，随机 `_test` 库 DROP、owned 容器已清理。业务 engine 未传恢复函数，业务库无 DDL/DML、权限无变化、用户原服务未停启。LIKE 来源只在己方容器建立，原 SHOW CREATE 保存在快照；仅去除 assets5/versions1/media_refs2 条 FK，其余列/索引/CHECK/选项保持。此演练不证明完整外键恢复、业务恢复或 MinIO 字节。

业务资产/画布指纹及配置仍与成功备份相同，019 ready/missing=[]/changed=[]；34 条历史 unknown 保持，原 Stop/回填/verify strict idle 门禁不变，角色当前指针/版本/引用仍 0。目录、resolver 与角色共享候选尚未产品应用；真实角色来源/菜单、同版部署、公网首帧及其他视觉/生命周期/32ms/完整 R10/M0–M5 仍未通过。A–J、B 内容失败及 R23 未批准候选全部保持，收费/真实音频供应商/企业授权暂缓，goal active。


## 2026-10-11 K/v7 独立重复真实验收与备份审计

K 使用未改字节的 v7 三份源码和 source-v7 三源码归档（归档本来没有 manifest）；外部 K execution/temp/source_key/baseline 与4202独立，内部 J/v7 title/input_revision 沿冻结源码保留。准备 v1 snapshot.parent_sha256 与真实父文件不符，原记录保持；[准备 v2](../../.runtime/agnes-storyboard-media-k-v7-preparation-v2/manifest.json)只纠父 provenance。两辅助脚本 LF→CRLF 逐字节重构验证、PowerShell Parser errors0、三 v7 live/archive/candidate hash 在[独立准备复核 v2](../../.runtime/agnes-storyboard-media-k-v7-peer-preparation-v2/receipt.json)通过；peer首轮保留换行文本比较 OS1 保留，v2明确核换行后通过，未调用供应商。

新 baseline 实际 OS0 后，K 入口 UTC 04:20:22.765333–04:24:03.655481，实际 **OS1 / pytest Exit / 1 warning，207.48s**。[原执行](../../backend/.runtime/agnes-storyboard-media-broker-live-k-v7-1440-execution.json)及[外层 OS](../../backend/.runtime/agnes-storyboard-media-k-v7-launcher-os.json)各自保留。红图 `367527115685183488`、蓝图 `367527131107639296` 各一次 POST200，均 succeeded/READY/attached；两图活动/终态 cookies-only 恢复和原字节完成。原红视频 `367527350700425216` 一次 POST503 `provider_queue_full`、原蓝视频 `367527358094983168` 一次 POST429 `provider_rate_limit`；冷却后原界面唯一首次重试 `367527671065559040` 一次 POST503 `provider_queue_full`。均明确未受理、无 provider ID/视频输出。driver618/616 → retryOriginalKnownRejections764 → runStage984 在首 retry 未真实 running 即停止，镜 3 retry 未执行。两个原拒绝的 early queued 原件保持，再1轮观察实际原生 ACK/GET 匹配 failed，未复用早期 ACK。

自然 reconciliation 为5 tasks/5 records/2 attached results，active/unknown/read_errors空，无额外POST/retry/resume/bind。Root [原件内容复核](../../backend/.runtime/agnes-storyboard-media-content-review-k-v7-1440.json)实际打开两张1024²原PNG和九浏览器PNG：完整单一红立方体/蓝实体圆柱，表面空白、无可见字/标识/人物/额外道具；仅几何小场 `passed_limited`、浏览器 `partial`、整轮 `not_passed`。截图两个submitting不单独证明供应商running；原拒绝及retry均为失败。视频活动/终态恢复、播放/字节/内容，以及完整成功Python、成员媒体权限、duplicate/stale AMQP后置均未执行。

[K 独立原件/资源审计](../../.runtime/agnes-storyboard-media-k-v7-peer-audit-v1/receipt.json)实际OS0，34原件审后清单/hash保持；11记录PID、4202/50114/50864、精确容器、11队列/2exchange/3实际桶均不存在。15原用户StartTime、两源HEAD/clean、11产品hash、三v7 live/archive/candidate、77旧输入与J审计收据保持。Node/Vite初始精确PID仍未记录，不从当前标记或端口补造历史身份；审计成功不等于供应商/内容验收通过。

[角色备份 v3 独立限定审计](../../.runtime/character-v4-rollout-package-v3-peer-backup-audit-v1/receipt.json)实际OS0：7执行原件、31冻结包文件hash与v2两复用收据身份保持，原Backup没有重跑；outer/stage/data/owned-cleanup均0、快照SHA一致。精确owned Docker full ID/name/label的三次只读查询当前为空；回读相等/随机库DROP依据已执行原收据和冻结实现，peer不重建恢复演练。16资产/两版本表0，5/1/2原FK文本保持而演练排除，不声称业务/FK完整恢复。BackupVerify.executed=false是原脚本的服务/回填写操作标志，不能据此否认已经实际执行的数据恢复阶段。保护计数34为sent/unknown合计；此前逐条只读核为34 unknown，本次peer只核合计保持，不宣称重新逐条核查或供应商确认。

角色目录/resolver/共享候选和真实MySQL/MinIO验证继续准备，尚未应用这些候选；旧来源仍不推导、声音不接、用户七角色未停启，34 unknown严格维护门禁保持。A–K及B内容失败、R23未批准候选保持，收费/真实音频供应商/企业授权暂缓；完整R10/M0–M5仍 `not_passed`，goal active。


## 2026-10-11 L/v7 三次视频503的真实验收边界

L 两辅助入口经[Root准备复核](../../.runtime/agnes-storyboard-media-l-v7-root-review-v1/receipt.json)后，以未改字节的J/v7三份源码独立执行；外部L来源键、execution/temp、4203及baseline独立，内部J/v7标题与input_revision沿冻结原件保留。启动前baseline实际OS0。UTC 04:35:44.073479–04:39:27.990026，[正式执行](../../backend/.runtime/agnes-storyboard-media-broker-live-l-v7-1440-execution.json)和[外层OS](../../backend/.runtime/agnes-storyboard-media-l-v7-launcher-os.json)都为1；pytest实际 **Exit / 1 warning，210.95s**。

| 任务 | 应用任务ID | 真实供应商响应 |
| --- | --- | --- |
| 红图 | `367530995504463872` | 一次POST200；succeeded/READY/attached |
| 蓝图 | `367531012571086848` | 一次POST200；succeeded/READY/attached |
| 原红视频 | `367531226270875648` | 一次POST503 / provider_queue_full |
| 原蓝视频 | `367531234072281088` | 一次POST503 / provider_queue_full |
| 红视频首次原UI重试 | `367531554072510464` | 一次POST503 / provider_queue_full |

三个视频均无供应商任务ID、输出、下载或轮询；本轮没有POST429，不能沿用K的429结论。原重试控件只点击一次，记录的冷却间隔65010ms；首retry未观察真实running，driver618/616→retryOriginalKnownRejections764→runStage984停止，镜3retry未执行。早期原保存观测是首镜running/loading、第三镜queued/loading，原件保留；随后一轮取得revision22两failed的实际原生ACK与GET，`reused_matching_early_ack=false`，没有拿早期ACK充当终态保存证明。

两图活动及终态cookies-only恢复、原字节回读与attached回填完成。Root [本轮内容复核](../../backend/.runtime/agnes-storyboard-media-content-review-l-v7-1440.json)逐一实际打开两原PNG和全部九浏览器PNG：完整单一红立方体、蓝实体圆柱，表面空白，无可见字/标识/人物/额外道具，几何小场 `passed_limited`。活动恢复截图已到红图completed/100%、蓝图saving/0%，不假称两个submitting；终态两图可见。视频拒绝和唯一retry截图均队列满失败，进行中标签不证明供应商running。浏览器 `partial`、整轮 `not_passed`；视频活动/终态恢复、字节/播放/内容与完整成功Python、成员媒体权限、duplicate/stale AMQP后置未执行。

自然reconciliation为5tasks/5records/2attached results，active/unknown/read_errors空，无新增POST/retry/resume/bind。[独立原件/清理审计](../../.runtime/agnes-storyboard-media-l-v7-peer-audit-v1/receipt.json)实际OS0（5.73s）：34原件清单/hash保持；11记录PID、4203/61406/61492、精确MySQL容器、11queues/2exchanges/3实际桶均不存在；15原用户服务StartTime、两源HEAD/clean、11产品hash、三v7 live/archive/candidate、77旧输入及J/K收据保持。Node/Vite初始精确PID仍未保存，不补造历史身份。清理通过不等于真实供应商验收通过。

角色目录/resolver真实隔离MySQL/MinIO7项此前已通过，仍只证明读取范围，非浏览器cookie/UI或复制/pinned/生成/助手验收。产品应用包补齐实际目录前端DTO，范围由17变18文件，尚未应用；共享发布候选继续隔离核验。角色回填/七角色同版部署/真实公网首帧、其他视觉/生命周期/32ms和完整R10/M0–M5仍未通过；34历史unknown严格维护门禁、A–L及B内容失败、R23未批准候选保持，收费/真实音频供应商/企业授权暂缓，goal active。


## 2026-10-11 M/v7 真实原件与角色发布候选接续

经[M准备复核](../../.runtime/agnes-storyboard-media-m-v7-preparation-v1/root-review-v1.json)，M使用独立4204、baseline、来源键与执行目录，完整保留J/v7三冻结源码和内部标题。UTC 05:25:47.957984–05:29:18.908370，[原正式执行](../../backend/.runtime/agnes-storyboard-media-broker-live-m-v7-1440-execution.json)记录即时LASTEXITCODE=1；[外层OS收据](../../backend/.runtime/agnes-storyboard-media-m-v7-launcher-os.json)是session7739实际返回OS1的Root转录，明确不是新造的原始入口日志。pytest **Exit / 1 warning，197.00s**，原失败保持。

| 任务 | 应用任务ID | 真实供应商响应 |
| --- | --- | --- |
| 红图 | `367543584993132544` | 一次POST200；succeeded/READY/attached |
| 蓝图 | `367543575539171328` | 一次POST200；succeeded/READY/attached |
| 原红视频 | `367543764719058944` | 一次POST503 / provider_queue_full |
| 原蓝视频 | `367543771866152960` | 一次POST503 / provider_queue_full |
| 红视频首次原UI重试 | `367544093925785600` | 一次POST503 / provider_queue_full |

三个视频均无供应商任务ID、结果、下载或轮询；本轮没有429，不沿用其他轮次结论。原重试控件只点击一次，原记录冷却间隔65008ms（最低65000ms）。首retry再次明确未受理，无法观察真实running，实际断言链为：

```text
AssertionError [ERR_ASSERTION]: 首个重试未能观察到真实 running，停止
verify-agnes-storyboard-media-python-v7.mjs:618
→ recoverFirstAcceptedRetry:616
→ retryOriginalKnownRejections:764
→ runStage:984
```

复现入口是在backend工作目录执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ../.runtime/run-agnes-storyboard-media-m-v7.ps1`；M已失败的原件不能覆盖，新轮使用新前缀。Babel超过500KB的note夹在pytest退出信息中，不是上述断言的根因。镜3retry未执行，视频活动/终态恢复、字节/播放/内容与完整成功Python、成员媒体权限、duplicate/stale AMQP后置未执行。

早期原保存观测完整保留；后续一轮取得两原视频failed的实际原生POST200 ACK与GET：expected_row_version19→graph_revision20、`reused_matching_early_ack=false`、原已保存状态可见。两图活动及终态cookies-only恢复、原字节回读与attached回填完成。Root [内容复核](../../backend/.runtime/agnes-storyboard-media-content-review-m-v7-1440.json)实际逐一打开两原1024²PNG和全部九浏览器PNG：完整单一红立方体、蓝实体圆柱，白色桌面、柔和光，表面空白，无可见字/标识/人物/额外道具，几何小场 `passed_limited`。活动恢复截图两图显示completed/100%但仍为loading占位，随后两终态截图均显示作品；不将恢复截图名或进行中标签当作供应商running。浏览器 `partial`、整轮 `not_passed`。

自然reconciliation为5tasks/5records/2attached results，active/unknown/read_errors空，无新增POST/retry/resume/bind。[独立原件/清理审计](../../.runtime/agnes-storyboard-media-m-v7-peer-audit-v1/receipt.json)实际OS0，直接核34原temp文件及execution/log/baseline；11精确已记录PID、4204/59861/59962、精确40acad前缀MySQL容器、11queues/2exchanges/3实际桶均不存在；15原用户服务PID/StartTime、两源HEAD/clean、11产品hash、三v7 live/archive/candidate与77历史输入保持。初始Node/Vite精确PID未保存，不补造历史身份。[进程marker读取边界](../../.runtime/agnes-storyboard-media-m-v7-peer-audit-v1/process-marker-read-boundary-v1.json)明确旧方法在内存读取WMI CommandLine再筛选，不输出或保存原命令行；若要求事前排除原服务读取，该更窄限制未满足。原服务身份保证仅据精确PID/StartTime，marker和空端口只支持当前限定范围。清理通过不等于真实供应商验收通过。

已应用20文件的角色目录/resolver仍以[最终验证记录](2026-10-11-beeftv-character-reads-audit.md)为准。共享发布[v2独立审查](../../.runtime/beeftv-character-publication-peer-qa-v2/final-readonly-receipt-v2.json)整体not_pass：crossretry和cover受控拒绝/回滚通过，但源Image角色设计工作流无身份探针2红/1绿。冻结[v3](../../.runtime/beeftv-character-publication-candidate-v3/manifest.json)仅改 `character_node`：Text+workflowKind识别角色卡，任一身份键存在（含空/None）仍进入严格门禁；普通Image/noID兼容保存。源builder只有定义和测试调用，目标无builder，不据此声称当前UI操作可达。原12after中只有classifier与两个测试文件变化，190支撑模块及额外package保持；原v1/v2/stage-v4库存与所有红结果保留。

v3最终实际320后端、14Node、strict TypeScript、Ruff/check/format、identity及影子patch均通过；有效红为2 failed/11 passed，另一个早期夹具断言错误单独保留。[Root最小差异审查](../../.runtime/beeftv-character-publication-v3-root-review-v1.json)实际OS0；[独立v3窄矩阵](../../.runtime/beeftv-character-publication-peer-qa-v3/final-readonly-receipt-v3.json)实际10 passed / 16.05s / OS0，无skip：两Image正常、Text与非法cover拒绝/全表回滚、私人参数和crossretry保留。v3未产品应用，真实隔离MySQL stage-v5仅准备，不执行旧stage-v4或把SQLite锁顺序当并发验证。17路径current角色UI候选继续隔离实施；可信旧来源/回填、同版七角色部署、公网首帧及完整R10/M0–M5仍未完成，goal active。34历史unknown不自动认定未受理或重发，A–M及B内容失败、R23未批准候选保持，收费/真实音频供应商/企业授权暂缓。


## 2026-10-11 N/v7 真实失败与角色发布 v5/v6 并发观测

N 复用冻结 J/v7 三份源码，使用独立4205、来源键及证据目录，内部标题仍J/v7。Root launcher session61094 实际返回 **OS1**；pytest **Exit / 1 warning / 197.50s**。两个图片请求均真实POST200，任务succeeded/READY，两个结果attached；活动与终态cookies-only恢复及原字节核对完成。两原视频和原界面唯一首次显式retry均POST503 `provider_queue_full`，没有供应商任务ID、GET、429或视频产物。首retry未观察真实running即停止，镜3retry未执行。Babel大文件提示不是失败断言原因。

| 内容 | 应用任务ID | 原供应商结果 |
| --- | --- | --- |
| 红立方体图片 | `367550601619128320` | POST200，825311bytes，1024²PNG |
| 蓝实体圆柱图片 | `367550610209062912` | POST200，771999bytes，1024²PNG |
| 原红视频 | `367550804984152064` | POST503，0输出 |
| 原蓝视频 | `367550811959279616` | POST503，0输出 |
| 红视频唯一UIretry | `367551133691756544` | POST503，0输出 |

原early ACK/graph revision21尚未表达两视频failed；随后原生commit **expected22→revision23**，stored23，`reused_matching_early_ack=false`、saved状态可见。唯一retry只点击一次，距参考供应商POST结束65004ms（实际等待58242ms），保留原task/retry lineage，没有自动重新提交unknown。失败stack为 `verify-agnes-storyboard-media-python-v7.mjs:618 → eventually:71 → recoverFirstAcceptedRetry:616 → retryOriginalKnownRejections:764 → runStage:984 → entry:1049`，原错误是首重试任务failed/provider_queue_full。复现沿冻结入口使用新的独立证据前缀，不覆盖N失败原件。

Root实际打开两原PNG和全部九张浏览器PNG：限定单一红立方体/蓝实体圆柱、白桌柔和光、表面空白，无可见文字、标识、人物或额外道具；仅几何小场`passed_limited`。N活动恢复截图两图submitting/0%，随后终态截图显示作品；截图名称不充作供应商running证明。浏览器`partial`，整轮`not_passed`。自然reconciliation为5tasks/5records/2attached，active/unknown/read_errors空且无额外提交。视频恢复、播放、内容、完整成功Python、成员媒体权限和duplicate/stale AMQP后置未执行；本轮没有文本模型请求。

原件见[N execution](../../backend/.runtime/agnes-storyboard-media-broker-live-n-v7-1440-execution.json)、[N log](../../backend/.runtime/agnes-storyboard-media-broker-live-n-v7-1440.log)、[Root内容审查](../../backend/.runtime/agnes-storyboard-media-content-review-n-v7-1440.json)、[launcher返回边界](../../backend/.runtime/agnes-storyboard-media-n-v7-launcher-os.json)及[独立最终只读收据](../../.runtime/agnes-storyboard-media-n-v7-peer-audit-v1/final-readonly-receipt-v1.json)。独立直接核34原temp文件，11精确已记录PID、4205/54368/54455、精确MySQL名、11queues/2exchanges/3buckets均不存在；原15PID/StartTime、11产品、两源和三份live/archive/candidate源码保持。没有WMI/CommandLine读取；初始Node/Vite PID未保存，端口不存在不能补足该身份。launcher OS收据是Root实际工具返回的转录，不是补造raw entry log。

角色发布stage-v5已经实际执行，此前“仅准备”是启动前历史快照：**15 failed / 0 skipped / 17.28s / OS1**。13案把内部整数版本ID直接放入自由metadata，违反真实JSON字符串合同；1案生成记录引用不存在的config999，MySQL1452外键拒绝；另1案在实际Asset锁等待后出现真实产品Conflict。失败和清理原件全部保留，见[v5 execution](../../.runtime/beeftv-character-publication-mysql-stage-v5/execution-8356b3374adc41f9bd93b77fc8504fb6/execution.json)、[v5 log](../../.runtime/beeftv-character-publication-mysql-stage-v5/execution-8356b3374adc41f9bd93b77fc8504fb6/pytest.log)及[v5独立结案](../../.runtime/beeftv-character-publication-mysql-v5-peer-audit-v1/final-readonly-receipt-v1.json)。

fixture-only stage-v6从v5逐字节继承全部产品及三运行入口；仅改用真实`CatalogDetail.model_dump(mode="json")`、新增本人disabled/nullkey模型外键种子，并在原竞争案增加被动观测。Root session3622实际 **4 failed / 11 passed / 0 skipped / 39.09s / OS1**；33个原Assert AST不变。原竞争案实测`REPEATABLE-READ`、assets192000主键X记录WAIT；取得资产锁后，普通version SELECT使用新指针ID `367553695723716608`，DAO current实际返回null，触发 `publication.validate:172 → catalog._item:69 → current_snapshot_locked:117 → Conflict: 角色当前版本指针所属不匹配`。本次短路后未执行latest_number/references，不能称这两方法已被实测。没有改隔离级别、替换查询或放宽原`canvas_character_version_changed`期望。

另三红分别为绑定测试错误预期通用`ownership_immutable`（实际专用`canvas_generation_immutable`），以及两跨画布夹具复用默认`publish:<base>`键，第二画布提交即被`The key belongs to another request`拒绝；这两案尚未进入各自并发段。准确错误码/403及按画布区分新请求键的下一夹具版本正在准备，产品不变。DAO锁内版本读修复需另验新Version引用新Media、项目关联变化等RR边界，不以当前11项通过推断全门禁通过。

见[v6冻结manifest](../../.runtime/beeftv-character-publication-mysql-stage-v6/manifest.json)、[Root审查](../../.runtime/beeftv-character-publication-mysql-v6-root-review-v1.json)、[v6 execution](../../.runtime/beeftv-character-publication-mysql-stage-v6/execution-f758baf014a5401a878254be6beea9e7/execution.json)、[v6 log](../../.runtime/beeftv-character-publication-mysql-stage-v6/execution-f758baf014a5401a878254be6beea9e7/pytest.log)、[被动观测原件](../../.runtime/beeftv-character-publication-mysql-stage-v6/execution-f758baf014a5401a878254be6beea9e7/test_current_version_commit_during_asset_wait_rejects_old_work-a41b022a.json)及[v6独立最终收据](../../.runtime/beeftv-character-publication-mysql-v6-peer-audit-v1/final-readonly-receipt-v1.json)。随机库DROP、三桶删除及两精确owned容器清理实际完成，50284/50285不存在；原15服务、20已应用产品、两源和v3产品before保持。真实MySQL/MinIO与注入Actor不代表cookie/UI或真实供应商验收；publication-v3仍未应用，17路径角色UI仍隔离准备。

O下一轮已启动，结果尚待收齐；其v1错误替换源码文件名、v2遗留N证据前缀均在模型调用前被输入/覆盖门禁拦截，零模型调用，N未覆盖，旧入口原件保留，v3仅精确修完整证据前缀。已应用目录/resolver的2738后端与真实MySQL/MinIO7项历史结果保持，不重复冒称本轮验证。角色回填、七角色同版部署、公网首帧与完整R10/M0–M5仍未完成，goal active；34历史unknown、R23未批准候选及全部旧失败保持，收费、真实音频供应商、企业授权继续暂缓。


## 2026-10-11 O-v7 结案及角色发布 v7 实测结果

O 以独立 4206、来源键、baseline 和证据目录重复冻结 J/v7 三份源码；内部标题仍 J/v7，没有修改 C 几何验收标准。Root launcher session72848 实际返回 **OS1**；原 pytest 为 **Exit / 1 warning / 242.62s**。v1 源码名称替换错误与 v2 遗留 N 证据前缀均在 wrapper/模型调用前被门禁拦截，零供应商调用；旧入口、原失败转录和 N 原件保留。v3 仅精确修证据前缀，不能把 v1/v2 记作真实模型执行。

| 内容 | 应用任务 ID | 原供应商结果 |
| --- | --- | --- |
| 蓝实体圆柱图片 | `367556057192284160` | 一次 POST200，796502 bytes，1024² PNG |
| 红立方体图片 | `367556068126834688` | 一次 POST200，819215 bytes，1024² PNG |
| 原红视频 | `367556333953433600` | 一次 POST503 / `provider_queue_full`，0 输出 |
| 原蓝视频 | `367556343998791680` | 一次 POST429 / `provider_rate_limit`，0 输出 |
| 红视频唯一原 UI retry | `367556664510726144` | 一次 POST503 / `provider_queue_full`，0 输出 |

两图真实 succeeded/READY/attached，活动与终态 cookies-only 恢复、原字节及回填完成。三个视频均明确未受理，`accepted_unknown=false`，没有供应商任务 ID、GET、下载或视频产物。唯一原 UI retry 只点击一次，使用新 Idempotency-Key，保留原 task/retry lineage；距参考 POST 完成 65002ms，实际等待 56779ms，未自动重投 unknown。首 retry 未观察到真实 running 即停止，镜 3 retry 未执行。

关键失败及调用链为：

```text
AssertionError [ERR_ASSERTION]: 首个重试未能观察到真实 running，停止
status=failed, error_code=provider_queue_full, output_count=0
verify-agnes-storyboard-media-python-v7.mjs:618
→ eventually:71
→ recoverFirstAcceptedRetry:616
→ retryOriginalKnownRejections:764
→ runStage:984
→ entry:1049
```

已执行入口是在 backend 工作目录运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ../.runtime/run-agnes-storyboard-media-o-v7-v3.ps1`；不能覆盖 O 原件，新轮必须使用独立证据前缀。Babel 大文件提示不是上述断言的原因。早期 ACK/graph revision22 原件保留；随后真实原生 POST200 commit 使用 expected22→revision23，stored23，`reused_matching_early_ack=false`，没有把早期 ACK 当作后续 failed 保存证明。

Root [内容复核](../../backend/.runtime/agnes-storyboard-media-content-review-o-v7-1440.json)实际打开两张原 1024² PNG 和全部九张浏览器 PNG：单一红立方体/蓝实体圆柱、白桌柔和光、表面空白，未见文字、标识、人物或额外道具；仅该几何小场 `passed_limited`。活动恢复截图红图 saving/0%、蓝图 submitting/0%，随后终态截图显示作品；retry-started 截图为 queued/0%，不能据截图名称或进行中标签认定供应商 running。浏览器 `partial`，O 整轮 **`not_passed`**。自然 reconciliation 为 5 tasks/5 records/2 attached results，active/unknown/read_errors 均空，无额外 POST/retry/resume/bind。视频活动/终态恢复、字节/播放/内容、完整成功 Python、成员媒体权限和 duplicate/stale AMQP 后置均未执行；O 本轮未完成文本模型验收。

见 [O 原执行](../../backend/.runtime/agnes-storyboard-media-broker-live-o-v7-1440-execution.json)、[O 原日志](../../backend/.runtime/agnes-storyboard-media-broker-live-o-v7-1440.log)、[launcher 返回边界](../../backend/.runtime/agnes-storyboard-media-o-v7-launcher-os.json)和[独立最终只读收据](../../.runtime/agnes-storyboard-media-o-v7-peer-audit-v1/final-readonly-receipt-v1.json)（SHA `ae7cf737200035f67fd007b004deb3c6d8a668f2d34bb9e3e790340f54e6eea4`）。独立审计直接核 34 原 temp 文件，11 个已记录精确 PID、4206/55202/63459、精确 MySQL 名、11 queues/2 exchanges/3 buckets 均不存在；15 个原用户服务 PID/StartTime、11 相关产品文件、两源 HEAD/clean、三份 live/archive/candidate 和 77 个旧输入保持。没有 WMI/CommandLine 或进程 marker 读取。初始 Node/Vite 精确身份未保存，仅证明真实监听端口不存在，不能补造逐进程消失证明。清理通过不改变 O 的供应商验收结果；peer 没有视觉内容复核，也未使用 Root projection。

角色共享发布 fixture-only stage-v7 从 v6 保留 767 份 repository 原件，只修 `commit` 默认请求键按画布区分和绑定不可变测试的专用错误码，并新增一个 403 断言；显式键字符串语义保持，其他断言不变。Root session5699 实际 **OS1 / 1 failed / 14 passed / 0 skipped / 55.59s**，真实 MySQL/MinIO 已执行，零供应商调用。v6 原先未进入并发段的两跨画布案及绑定不可变案，在 v7 均真正通过：

- `test_task_binding_identity_remains_immutable_in_real_mysql`
- `test_multi_owner_batch_waits_lowest_source_before_any_later_project`
- `test_cross_canvas_retry_finishes_while_lower_scope_is_really_locked`

唯一失败仍是 `test_current_version_commit_during_asset_wait_rejects_old_work`：实测事务隔离为 `REPEATABLE-READ`，assets192000 PRIMARY 的 X 记录锁发生真实 WAIT；拿到资产锁后，普通 version SELECT 查新指针 ID `367557115637563392`，DAO current 实际返回 null，最终仍报 `Conflict: 角色当前版本指针所属不匹配`。关键 stack 是 `test_mysql_publication.py:878 → future.result → worker:626 → invoke:862 → CanvasService.commit:673 → _apply_document:706 → _validate_character_publications:745 → publication.validate:172 → catalog._item:69 → current_snapshot_locked:117`。没有改变隔离级别、替换查询或放宽原 `canvas_character_version_changed` 期望；latest_number/references 因 current-null 短路未被观测，新 Media 与项目关联变化也未验证。v7 整体仍为 **`not_passed`**，不能用 14 项通过替代这项 RR 门禁。

该竞争复现沿 v7 冻结的原用例和真实隔离入口，在新独占资源中阻塞资产锁、提交新的当前版本，再释放锁并观察原工作；原执行 argv 保存在下方 execution 收据，失败原件不覆盖。

原件见 [v7 manifest](../../.runtime/beeftv-character-publication-mysql-stage-v7/manifest.json)、[v7 execution](../../.runtime/beeftv-character-publication-mysql-stage-v7/execution-590fb16d16094e5db6ef213818312a93/execution.json)、[v7 log](../../.runtime/beeftv-character-publication-mysql-stage-v7/execution-590fb16d16094e5db6ef213818312a93/pytest.log)、[RR 被动观测](../../.runtime/beeftv-character-publication-mysql-stage-v7/execution-590fb16d16094e5db6ef213818312a93/test_current_version_commit_during_asset_wait_rejects_old_work-a41b022a.json)及[独立最终收据](../../.runtime/beeftv-character-publication-mysql-v7-peer-audit-v1/final-readonly-receipt-v1.json)（SHA `5d4ef922fecaf1fa3007d303e67f500ee6c91b546ad190a6131805f4ccd71408`）。随机库 DROP/三桶移除以原 finally 断言和 cleanup 收据为据；两个精确己方容器和 58367/58368 已回查不存在，未重启容器或补造新 SQL/桶查询。v5/v6 全部旧红、v7 原件、原 15 服务、20 个已应用目录/resolver 文件、两源及 publication-v3 的 12 个产品 before 保持。

17 路径 current 角色 UI [隔离候选](../../.runtime/beeftv-character-current-ui-candidate-v1/manifest.json)已冻结（manifest SHA `a045398fe08bddf15c3be9b6833cb3718c46ea6a131e7a3fef90a9c8d4bafe3a`），**未应用产品**。候选定向/画布/宿主 Node 为 26/466/219 passed，浏览器 API 夹具 4 passed；两包 strict、生产构建和 source 检查完成。五份较新收据包含全部 17 输入 SHA，最后 build/browser 输入保持；三份较早收据缺当时输入 SHA，未补造。纯补丁另在独占影子实际 `git apply --check`/apply 并核 17 after 字节一致；这些结果不代表真实 cookie、MySQL/MinIO copy/publication/共享 CAS、供应商媒体或固定源完整一比一验收。唯一隔离 tsbuildinfo 缓存变化和早期 Vite 在共享依赖目录写临时配置的边界见[候选说明](../../.runtime/beeftv-character-current-ui-candidate-v1/README.md)。

publication-v3 仍未应用；针对 RR 的 [DAO-only 锁内读取候选](../../.runtime/beeftv-character-version-current-read-candidate-v1/manifest.json) 已冻结（SHA `fa14e7f49f9aea00f68594f1564e10906593dd5c8f1839bb2306c05e18c54b83`），未应用；103 项 SQLite 版本单测、三条实际 SQL 的共享锁编译、补丁检查与 Ruff 通过，真实 RR 和 6 个新探针尚未执行，需先补新 Version/Media 与关联变化的真实并发证据，再由主流程审查候选及依赖门禁。已应用目录/resolver 的 2738 后端及真实 MySQL/MinIO 7 项是此前结果，本轮没有重跑。此前“O 尚待结果”“17 路径 UI 仍准备”均为历史快照。本次只读交叉复核核 72 份证据原件哈希/JUnit/PNG 元数据，未重跑模型或测试、未作新的视觉内容验收，见[复核收据](../../.runtime/remaining-gate-o-v7-maintenance-drafts-v1/readonly-review-receipt-v1.json)。随后新独立 P 轮已启动（4207/session45544，[准备 manifest](../../.runtime/agnes-storyboard-media-p-v7-preparation-v1/manifest.json)，SHA `af2e860cd02b21efe27c5aff0bb04c82d832ecd90f5b9c8ebf855e3ebc813b77`），此处仅为尚无结果的启动快照，O 原件与结案保持。角色回填、同版七角色部署、公网首帧与完整 R10/M0–M5 仍未完成，goal active；34 历史 unknown、A–O 及 B 内容失败、R23 未批准候选保持。收费、真实音频供应商和企业授权继续暂缓。


## 2026-10-11 P-v7 结案与 RR v2 实测未通过

P 使用独立 4207、来源键、baseline 和证据目录重复冻结 J/v7 三份源码；内部标题仍为 J/v7，C 几何内容标准没有变更。Root launcher session45544 实际返回 **OS1**，原 pytest 为 **Exit / 1 warning / 207.42s**。见 [P 原执行](../../backend/.runtime/agnes-storyboard-media-broker-live-p-v7-1440-execution.json)、[原日志](../../backend/.runtime/agnes-storyboard-media-broker-live-p-v7-1440.log)与 [launcher 返回边界](../../backend/.runtime/agnes-storyboard-media-p-v7-launcher-os.json)。launcher 收据是 Root 实际工具返回的转录，没有补造新的原始工具日志。

| 内容 | 应用任务 ID | 原供应商结果 |
| --- | --- | --- |
| 红立方体图片 | `367562478592016384` | 一次 POST200，728668 bytes，1024² PNG |
| 蓝实体圆柱图片 | `367562488968724480` | 一次 POST200，829441 bytes，1024² PNG |
| 原红视频 | `367562697685680128` | 一次 POST429 / `provider_rate_limit`，0 输出 |
| 原蓝视频 | `367562704308486144` | 一次 POST503 / `provider_queue_full`，0 输出 |
| 红视频唯一原 UI retry | `367563008726876160` | 一次 POST503 / `provider_queue_full`，0 输出 |

两图真实 succeeded/READY/attached，活动与终态 cookie-only 恢复、原字节和回填完成。Root [内容复核](../../backend/.runtime/agnes-storyboard-media-content-review-p-v7-1440.json)实际打开两张供应商原 PNG 和全部九张浏览器 PNG：白桌上的单一红立方体、蓝实体圆柱，柔和光照且表面空白，未见文字、标识、人物或额外道具；仅该几何小场 **`passed_limited`**。活动恢复截图两图均为 saving/0%，终态截图显示作品，不能扩展为其他内容或完整迁移验收。

三个视频请求均明确未受理，`accepted_unknown=false`；没有供应商任务 ID、GET、下载或视频产物。红视频唯一原 UI retry 只点击一次，使用新 Idempotency-Key，原 task/retry lineage 保持；距参考 POST 完成 **65013ms**，实际等待 **57755ms**。首 retry 未观察到真实 running 即停止，蓝视频 retry 未执行，未自动重投 unknown。retry-started 截图任务面板显示进行中，而节点仍显示失败/队列满；面板或截图名称不能证明供应商 running。

关键失败和调用链保存在原浏览器 report，摘录如下：

```text
AssertionError [ERR_ASSERTION]: 首个重试未能观察到真实 running，停止
task=367563008726876160, status=failed
error_code=provider_queue_full, output_count=0
verify-agnes-storyboard-media-python-v7.mjs:618
→ eventually:71
→ recoverFirstAcceptedRetry:616
→ retryOriginalKnownRejections:764
→ runStage:984
→ entry:1049
```

已执行入口为 backend 工作目录下的 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ../.runtime/run-agnes-storyboard-media-p-v7.ps1`；后续轮次须使用新的独立证据前缀，不覆盖 P 原件。早期 ACK/graph revision22 未同时表达视频 failed；随后真实原生 POST200 commit 使用 expected22→revision23，stored23，`reused_matching_early_ack=false`，没有将早期 ACK 当作后续 failed 保存证明。自然 reconciliation 为 **5 tasks / 5 records / 2 attached image results**，active/unknown/read_errors 均空，无新 POST/retry/resume/injection。浏览器为 `partial`，P 整轮仍为 **`not_passed`**。第二次视频 retry、视频活动/终态恢复、字节/播放/内容、完整成功 Python 后置、成员媒体权限及 duplicate/stale AMQP 后置均未执行；P 本轮未完成文本模型验收。

[独立最终只读收据](../../.runtime/agnes-storyboard-media-p-v7-peer-audit-v1/final-readonly-receipt-v1.json) SHA 为 `aeb10716f28b84e0b6c847d0c744d1dafdfb46ddc47176e620d13c82b0895b73`。独立审计直接核对 **34** 份原 temp 文件，11 个已记录精确 PID、61661/61749/4207、精确 MySQL 容器 `beeftv-resume-mysql-test-11954e9979a646b1a1f260d9f61ed40c`、11 queues/2 exchanges/3 buckets 均真实不存在；15 个原服务 PID/StartTime、11 个相关产品文件、两源 HEAD/clean、三份 live/archive/candidate 和 77 个旧输入保持。MinIO 通过内部 Settings/MinioStorage 对精确桶执行只读存在性查询，没有直接读取或输出 `.env` 内容。没有 WMI/CommandLine 或进程 marker 读取；初始 Node/Vite 精确身份未保存，只证明监听端口不存在，不补造逐进程消失证明。peer 未使用 Root projection，也未自行做内容视觉验收；本次维护草稿只核原件和已保存收据，没有新模型、测试或基础设施调用。清理证明不改变供应商验收结果。

RR [v2 冻结 manifest](../../.runtime/beeftv-character-version-rr-boundary-stage-v2/manifest.json) SHA 为 `6aa5b3a315fbe43bea1ffa04c933ee72edfb5722d02a6208e01ec50fb34cb51d`，[静态收据](../../.runtime/beeftv-character-version-rr-boundary-stage-v2/static-review.json) SHA 为 `7d4dac86c1e3a524a08e02f46f11e279b57237b30a1fec266ba77a20efb84de6`。779 份冻结文件与离线 collect-only **21 案 / 1.15s** 是准备验证，不运行测试。随后 Root 在独立真实 MySQL/MinIO 环境执行 v2，实际 **OS1 / 6 failed / 15 passed / 0 skipped / 48.93s**，零模型调用，候选未应用。见 [v2 原执行](../../.runtime/beeftv-character-version-rr-boundary-stage-v2/execution-90e79e072972423c8bd68aa889488025/execution.json)与 [pytest 原日志](../../.runtime/beeftv-character-version-rr-boundary-stage-v2/execution-90e79e072972423c8bd68aa889488025/pytest.log)。原 7 个函数参数化的 15 案实际通过；新增 3 个函数参数化的 6 案均实际失败，整轮仍为 **`not_passed`**：

- 新 Media 目录读取及 publication 两案抛出 `NotFound: 角色版本引用的媒体不属于角色范围`；publication 的精确 WorkflowError/409 业务断言未到达；finally 中完整图内容、revision 和表计数回滚断言仍实际执行通过，不能替代业务冲突验收。
- 合法项目关联 unlink/relink 的 get/list 四案仍读取旧视图：get-unlink 未抛期望 NotFound，get-relink 抛出“角色未关联到该来源项目”；list-unlink 仍返回 `292000`，list-relink 未返回期望 `302000`。合法关系前置已经补齐，不能归类为非法夹具操作或静态准备失败。

这些新增失败的关键调用链在原日志：新媒体 GET 经 `catalog.get → _item → current_snapshot_locked → _validate_media`，publication 经 `CanvasService.commit → _validate_character_publications → catalog._item → current_snapshot_locked → _validate_media`；关系变化案在原 `test_rr_catalog_relation_matches_legal_change_committed_during_project_wait` 的 get/list 期望处失败。Root 执行的完整 argv 保存在原 execution 收据，后续复现须使用新的独占资源和执行目录，不覆盖此轮原件。隔离容器删除以该原 execution 的精确 ID/owner/removed 记录为据；本次维护草稿没有进行新的资源查询。此前 v7 的 current-pointer 红和所有旧原件保留，v2 原 15 项通过只描述本轮隔离候选结果，不能替代新增 RR 门禁或应用批准。v1 仅在静态复核发现合法关系前置缺失，没有执行 MySQL/MinIO，不能称 v1 为一次真实基础设施失败。

[RR v2 与 P 独立结案](../../.runtime/remaining-gate-rr-v2-and-p-peer-audit-v1/final-readonly-receipt-v1.json) SHA 为 `77b43412860a0eeba9274478090ca30a2b27240f5829aea6421872cdcac8aed2`，核对 **1630** 份受保护原件前后哈希保持；精确两个隔离容器 ID 及 55659/55661 端口已回查不存在。该 peer 没有重跑测试或模型，本次文档作者仅读取已保存收据，不重复资源查询。

此前“P 尚无结果”为当时启动快照，此处记录 P 已有的失败结案。A–O、B 内容失败、34 条历史 unknown 及其他未批准候选保持；视频与完整 R10/M0–M5 仍未完成。收费模型、真实音频供应商和企业授权继续暂缓。


## 2026-10-11 Q-v7 结案与 RR v3 当前21案实测通过

Q 使用独立 4208、来源键、baseline 和证据目录重复冻结 J/v7 三份源码；内部标题仍为 J/v7，C 几何内容标准保持。Root launcher session67560 实际返回 **OS1**，原 pytest 为 **Exit / 1 warning / 202.69s**。见 [Q 原执行](../../backend/.runtime/agnes-storyboard-media-broker-live-q-v7-1440-execution.json)、[原日志](../../backend/.runtime/agnes-storyboard-media-broker-live-q-v7-1440.log)及 [launcher 返回收据](../../backend/.runtime/agnes-storyboard-media-q-v7-launcher-os.json)。launcher 是 Root 实际工具返回的转录，未补造原始工具日志。

| 内容 | 应用任务 ID | 原供应商结果 |
| --- | --- | --- |
| 红立方体图片 | `367568136460648448` | 一次 POST200，766820 bytes，1024² PNG |
| 蓝实体圆柱图片 | `367568143771320320` | 一次 POST200，796823 bytes，1024² PNG |
| 原红视频 | `367568359585038336` | 一次 POST503 / `provider_queue_full`，0 输出 |
| 原蓝视频 | `367568366950236160` | 一次 POST429 / `provider_rate_limit`，0 输出 |
| 红视频唯一原 UI retry | `367568674090729472` | 一次 POST503 / `provider_queue_full`，0 输出 |

两图真实 succeeded/READY/attached，活动与终态 cookie-only 恢复、原字节和回填完成。红图原 PNG SHA 为 `d597020c869235d8d0b308a645e4549a9187a75722a36f976b9d56daf565deab`，蓝图为 `5dcd0a9c24cf5ee85385785ffd063cb605ba3999a3901ec31d939bf5e9a41bd0`。Root [内容复核](../../backend/.runtime/agnes-storyboard-media-content-review-q-v7-1440.json)实际打开两张供应商原 PNG 和全部九张浏览器 PNG：白桌上的单一红立方体、蓝实体圆柱，柔和光照且表面空白，未见文字、标识、人物或额外道具；仅该几何小场 **`passed_limited`**。活动恢复截图红图 saving、蓝图 submitting，均 0%；终态截图显示正确作品。独立 peer 未自行做视觉验收，不能扩展为其他内容或完整迁移验收。

三个视频请求均明确未受理，没有供应商任务 ID、GET、下载或视频产物。唯一原 UI 红 retry 只点击一次，使用新 Idempotency-Key，原 task/retry lineage 保持；距参考 POST 完成 **65001ms**，实际等待 **57644ms**。首 retry 未观察到真实 running 即停止，第二个原视频 retry 未执行，未自动重投 unknown。retry-started 截图两节点仍为失败、分别提示队列满和请求过频，没有可见任务面板；截图名称不能证明供应商 running。

关键失败和调用链摘自原浏览器 report：

```text
AssertionError [ERR_ASSERTION]: 首个重试未能观察到真实 running，停止
task=367568674090729472, status=failed
error_code=provider_queue_full, output_count=0
verify-agnes-storyboard-media-python-v7.mjs:618
→ eventually:71
→ recoverFirstAcceptedRetry:616
→ retryOriginalKnownRejections:764
→ runStage:984
→ entry:1049
```

已执行入口为 backend 工作目录下的 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ../.runtime/run-agnes-storyboard-media-q-v7.ps1`；后续轮次须使用新的独立证据前缀，保留 P、Q 原件。early graph revision22 未同时表达视频 failed；随后真实原生 POST200 commit 使用 expected22→revision23，stored23，`reused_matching_early_ack=false`，原生 saved 可见，没有将早期 ACK 当作后续 failed 保存证明。自然 reconciliation 为 **5 tasks / 5 records / 2 attached image results**，active/unknown/read_errors 均空，无新 POST/retry/resume/injection。浏览器为 `partial`，Q 整轮仍为 **`not_passed`**。第二次视频 retry、视频活动/终态恢复、字节/播放/内容、完整成功 Python 后置、成员媒体权限及 duplicate/stale AMQP 后置未执行；Q 本轮未完成文本模型验收。

[Q 独立最终只读收据 v2](../../.runtime/agnes-storyboard-media-q-v7-peer-audit-v1/final-readonly-receipt-v2.json) SHA 为 `bd74ee895a6c4416d78822ffd9cf86d880b58b1ff1d744e8e08ccbb6f56fe6da`，原 v1 保留。该审计核对 **34** 份原 temp 文件保持；11 个已记录精确 PID、4208/53726/59354、精确 MySQL `beeftv-resume-mysql-test-d3668b22d34447c29af3fde3428d59e6`、11 queues/2 exchanges/3 buckets 均不存在；15 个原用户服务 PID/StartTime、11 个相关产品文件和两源 HEAD/clean 保持。初始 Node/Vite 精确身份未保存，只证明端口无监听，不补造逐进程消失证明。初始总库存 **42** 项，终态 **44** 项：两个 launcher/snapshot 文件在初始库存保存后加入内存保护，原 42 项逐项匹配，额外两项按准备和最终哈希核验；不能称 44 项均有初始库存记录。未使用 WMI/CIM、CommandLine/marker 或 Root projection；未读取或输出凭据。本次维护候选只核原件及已保存收据，没有新模型、测试或基础设施查询，清理不改变验收结果。

RR [v3 冻结 manifest](../../.runtime/beeftv-character-version-rr-boundary-stage-v3/manifest.json) SHA 为 `509cb981140f33e558c641fe6de7d7f1bfe0f5e270fb6611d310422b0890b81e`，[静态收据](../../.runtime/beeftv-character-version-rr-boundary-stage-v3/static-review.json) SHA 为 `fab7a432dda3302d8c0201c32c89a5ae0013d6bab9400990e7fbc567b35540fb`，包含 778 份冻结文件。其 21 案测试源码与 frozen_runtime/run_isolated/static_gate 三入口逐字节等于 v2。v3 只在冻结 repository 内追加两个文件的当前读取影子：`canvas_character_catalog_dao.py` 的 relation/relations/media 与 `asset_version_service.py` 的 _validate_media；既有 AssetVersionDAO current/latest/references 三读保持。产品源码及 publication/UI/DAO 等候选仍冻结未应用。

随后 Root 在独立真实 MySQL/MinIO 环境执行 v3，session17881 实际 **OS0 / 21 passed / 0 skipped / 47.01s**，零模型调用、产品未应用。见 [v3 原执行](../../.runtime/beeftv-character-version-rr-boundary-stage-v3/execution-be18f41f8c9d407fa11883fd57f58625/execution.json)、[pytest 原日志](../../.runtime/beeftv-character-version-rr-boundary-stage-v3/execution-be18f41f8c9d407fa11883fd57f58625/pytest.log)与 [原 JUnit](../../.runtime/beeftv-character-version-rr-boundary-stage-v3/execution-be18f41f8c9d407fa11883fd57f58625/junit.xml)。原 v2 六红在相同测试条件下全部转绿：新 Media 目录读取、publication 精确 WorkflowError/409 与完整回滚，以及合法 unlink/relink 的 get/list 四案均通过；旧 v2 实际失败记录原字节保留。六新增案的实际观察为 REPEATABLE-READ/source Project X record WAIT，relation SQL 使用 FOR SHARE，Media SQL 使用 FOR UPDATE，Actor 范围过滤保留。完整 argv 仍在原 execution；后续复现须使用新独占资源和执行目录，不覆盖本轮。

[RR v3 独立结案](../../.runtime/beeftv-character-version-rr-v3-peer-audit-v1/final-readonly-receipt-v1.json) SHA 为 `5c5d861db5b0c62a96592f4bc484eb2c62f1bd435a05d52023a83a66f9ee6479`，核 v3 **807** 份原件及旧 v2/P **1630** 份保护输入前后保持；精确 MySQL `canvas-publication-mysql-be18f41f8c9d407fa11883fd57f58625`、MinIO `canvas-publication-minio-be18f41f8c9d407fa11883fd57f58625` 的两 ID 均不存在，56018/56019 无监听。[原 SQL/storage 清理收据](../../.runtime/beeftv-character-version-rr-boundary-stage-v3/execution-be18f41f8c9d407fa11883fd57f58625/sql-storage-cleanup.json)记录临时库 DROP、三桶删除、errors 空。本次文档作者只核保存收据，没有重新查询、启动或清理资源。

**21 项真实通过仅限当前冻结候选的并发门禁**，没有证明正常集成入口、完整产品或迁移、供应商视频验收，也没有证明 cached existing-media 身份刷新、dirty publication 或完整既有 Media S→X/并发 confirm 调用链。[v4 25 案准备 manifest](../../.runtime/beeftv-character-version-rr-boundary-stage-v4/manifest.json)及 [静态收据](../../.runtime/beeftv-character-version-rr-boundary-stage-v4/static-review.json)中的 collect25 是 **原21 + 四新增**：_parent 新 Media append、dirty 名称真实 flush/version2、Images 新 Media resource_map/PNG 回读、same-ID binary 精确409；其终态属于后续独立轮次，不计入本节 v3 通过范围。该四案包同样不覆盖共享既有 Media 的完整 S→X 并发 confirm 实证。正常集成七案的准备/collect/lint 也不等于正常入口真实通过。

本节在实际已应用 P/RR-v2 的文档末尾追加，旧 P、v2 失败及历史启动快照不改写。A–P、B 内容失败、34 条历史 unknown 和其他未批准候选保持；真实视频及完整 R10/M0–M5 仍未完成。收费模型、真实音频供应商和企业授权继续暂缓。


## 2026-10-11 R-v7 与 RR v4 至 v6 失败结案

R 以独立 4209 重复冻结 J/v7，Root session23503 实际 **OS1 / pytest Exit / 1 warning / 210.70s**；见 [原执行](../../backend/.runtime/agnes-storyboard-media-broker-live-r-v7-1440-execution.json)及 [原日志](../../backend/.runtime/agnes-storyboard-media-broker-live-r-v7-1440.log)。红图 `367576055927357440`（791335 bytes）、蓝图 `367576066484420608`（805340 bytes）各真实 POST200，1024² PNG，succeeded/READY/attached。Root [内容收据](../../backend/.runtime/agnes-storyboard-media-content-review-r-v7-1440.json)仅判红立方体/蓝实体圆柱几何小场 `passed_limited`；独立 peer 核原字节与尺寸，未自行视觉验收。原红视频 `367576283162165248`、原蓝 `367576292888756224`、唯一原 UI 红 retry `367576624381378560` 分别 POST503 队列满 / POST429 限流 / POST503 队列满，均无供应商 ID、GET、下载或产物。retry 点击1次、新幂等键、lineage保持，距参考POST65013ms，实等58099ms；无真实running即停，第二视频retry未执行。late 原生 ACK expected23→revision24/stored24，不复用early22，saved可见；5tasks/5records/2attached，active/unknown/read_errors空。Root直接打开九张原截图；retry/failure虽有1进行中任务面板，两节点仍失败，不能当作供应商running。视频恢复/播放/内容、完整成功Python、成员权限、duplicate/stale AMQP及本轮文字验收均未达，整轮 **not_passed**。

R [独立最终只读收据 v3](../../.runtime/agnes-storyboard-media-r-v7-peer-audit-v1/final-readonly-receipt-v3.json) SHA `b4211780709a3dc251ba2c6a5108c7cabd6f74bd44280e7d6853e2479dac69c6`：34原件及200保护输入、旧Q44库存前后保持；11已记录精确PID、4209/63472/63562、MySQL `beeftv-resume-mysql-test-a9a59a775e3f4a689381ef665566013a`、11queues/2exchanges/3buckets不存在；15既有用户PID/StartTime、11产品及两源HEAD/clean保持。Node/Vite初始精确身份缺失，只证4209无监听；无WMI/CIM/CommandLine/marker或projection使用。Root review首v1因省略errors字段KeyError，空目录与脚本保留，另v2成功；peer自己的错误启动路径、截图字段假定及派生路径错误也全部保留，v3纠正后真实OS0，不改变供应商失败。原失败stack为 driver618→eventually71→recoverFirstAcceptedRetry616→retryOriginalKnownRejections764→runStage984→entry1049；已执行 backend 下 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ../.runtime/run-agnes-storyboard-media-r-v7.ps1`，后续必须新前缀，保留Q、R原件。

RR后续三轮均为**25案=原21+四新增**，真实隔离MySQL/MinIO、0skip、0模型调用，候选未应用；三轮结果不同，旧21绿不能替代新增边界：

| 轮次 | Root实际结果 | 新增边界终态 |
| --- | --- | --- |
| [v4原执行](../../.runtime/beeftv-character-version-rr-boundary-stage-v4/execution-64c7d7c2ef7d4b619b6d0ac4767caf2e/execution.json) | OS1，3failed/22passed，51.88s | append在reference_media_ids before_flush普通读报NotFound，_parent未达；Images资源map误ambiguous409；同IDbinary夹具漏updated_by报1048，业务冲突未达 |
| [v5原执行](../../.runtime/beeftv-character-version-rr-boundary-stage-v5/execution-407dfe2a53e046c3bfeeccad0999391d/execution.json) | session19048 OS1，3failed/22passed，61.54s | 仅补binary.updated_by，25案/14函数AST、三入口和repository保持；同IDbinary改为真实DID NOT RAISE WorkflowError，另外两产品红保持 |
| [v6原执行](../../.runtime/beeftv-character-version-rr-boundary-stage-v6/execution-7e69f4a923894d219c537b0f585e3a79/execution.json) | session5519 OS1，1failed/24passed，50.10s | Images新Media map/PNG回读及同IDbinary精确409转绿；append首次真正到达_parent(MediaFile)，仍报“角色版本引用的资源不存在”NotFound |

v5 [独立结案](../../.runtime/beeftv-character-version-rr-v5-peer-audit-v1/final-readonly-receipt-v1.json) SHA `f0ede72dc310f7ec247641057dffb7e0f7b0f35591a940fe6151e7b4201e3dff` 核v4 813+v5 812=1625原件保持，精确两ID及63581/63582不存在；原库/三桶清理无error。binary实际失败时finally完整图回滚通过，不能替代精确409；dirty名称真实flush/version2一案通过，不能扩展为dirty publication或完整既有Media S→X并发confirm。v4/v6两owned容器清理以各原execution的owner_verified/removed记录为据，本维护作者未重查其资源。v6原失败链为 `append_locked:208 → guard_writes:981 → guard_version_writes:88 → _parent:54`；原 [pytest日志](../../.runtime/beeftv-character-version-rr-boundary-stage-v6/execution-7e69f4a923894d219c537b0f585e3a79/pytest.log)与 [JUnit](../../.runtime/beeftv-character-version-rr-boundary-stage-v6/execution-7e69f4a923894d219c537b0f585e3a79/junit.xml)保留。完整argv见各execution；任何复现使用新独占资源/目录，禁止覆盖旧轮。正常集成入口、完整产品/迁移及供应商视频尚未由这些轮次验收；publication/UI/DAO等仍冻结未应用。后续v7另轮记录，不等待、不预写结果。旧失败、34历史unknown保持，收费/真实音频/企业授权继续暂缓。


### RR-v7 与正常七案回归及七文件应用

本条追加 R 与 RR-v4–v6 记录之后的证据，不改写此前失败。R 真实供应商整轮仍为 `not_passed`，视频供应商的排队/限流结果没有被数据库回归替代。

- **RR-v6 → v7**：v6 原执行为 OS1、1 failed / 24 passed / 0 skipped / 50.10s；唯一失败已真正进入 `append_locked:208 → guard_writes:981 → guard_version_writes:88 → _parent:54`，报“角色版本引用的资源不存在”。v7 只将 `_parent` 的 Media 查询改为当前 X 读，保留 `session.new`、Asset/AssetVersion 分支及 dirty 状态，未加 `populate_existing`。完整原 25 案源码、断言和三个执行入口保持，真实 MySQL/MinIO 原执行为 OS0、25 passed / 0 skipped / 52.18s；Images 的新 Media PNG 回读、同 ID Binary 精确 409 及 dirty 名称路径继续通过。见 [v6 原执行](../../.runtime/beeftv-character-version-rr-boundary-stage-v6/execution-7e69f4a923894d219c537b0f585e3a79/execution.json)、[v7 原执行](../../.runtime/beeftv-character-version-rr-boundary-stage-v7/execution-9288e3aa762a475ba897f935f047d02b/execution.json)、[v7 原日志](../../.runtime/beeftv-character-version-rr-boundary-stage-v7/execution-9288e3aa762a475ba897f935f047d02b/pytest.log)及[独立终态收据](../../.runtime/beeftv-character-version-rr-v6-v7-peer-audit-v1/final-readonly-receipt-v1.json)。
- **正常 integration 入口七案**：保持同一测试、正常 `scripts/run_integration.py` 与两级 conftest；原 red 为 pytest1、7 failed / 0 passed / 0 skipped / 10.55s，green 为 pytest0、7 passed / 0 skipped / 9.56s。七案均实际使用 RR、可信 Actor 与独立连接，并观测到真实 WAIT；150 个产品模块各自来自对应冻结影子，没有导入单元测试 fixture 或 runtime probe。旧卡精确 409、完整图状态及新定义/关联变化在 green 通过。两次外层仍 OS1，原 `cleanup.removed=false` 保留：清理断言把实际小写 `no such object` 当作失败；Root 随后只读证两精确 ID/端口消失，未再次删除，也未把补录改成新 OS0。见 [正常 red 原执行](../../.runtime/beeftv-character-rr-normal-entry-red-green-v1/execution-red-9e2aa5d6f9f444279ed1510d1cd4a5c3/execution.json)、[正常 green 原执行](../../.runtime/beeftv-character-rr-normal-entry-red-green-v1/execution-green-dd4497db15cd4ab9949e40fdda086cd9/execution.json)、[Root 实际返回及清理补录](../../.runtime/beeftv-character-rr-normal-entry-red-green-v1/root-actual-returns-and-cleanup-v1.json)及[正常七案独立收据](../../.runtime/beeftv-character-rr-normal7-peer-audit-v1/final-readonly-receipt-v1.json)。
- **实际产品应用与后验证**：七个当前读修复文件已按[应用收据](../../.runtime/beeftv-character-current-reads-application-v1/application.json)落入工作区；此前影子执行的 `product_applied=false` 只描述当时，不能据此否认此次合法应用。本次不包含 publication/UI 候选或数据库 DDL。[应用后验证收据](../../.runtime/beeftv-character-current-reads-verification-root-v1/receipt.json)记录相关三组 unit、两组 API 为 OS0、171 passed / 0 skipped / 2 warnings / 106.49s，全仓 Ruff check OS0，七文件 format OS0。全仓 format 仍 OS1，唯一既有范围外失败是 `scripts/apply_assembly_migration.py`，807 文件已格式化，未修改该脚本。收据引用实际 session/chunk，pytest 输出没有另行重定向为完整原日志；当时全 unit/API 仍在运行，不能提前记为通过。
- **保全与边界**：独立只读回查 RR-v6/v7 四精确容器 ID、四端口均不存在；两轮 SQL `_test` 库与三桶清理核原收据，1619 件原件前后保持。正常七案 1562 件原件保持，正常 fixture 两个随机 `_test` 库的 DROP 核原收据，容器/端口终态引用 Root 补录。独立格式探测曾 OS1 `KeyError: call_path`，已保留[方法失败记录](../../.runtime/beeftv-character-version-rr-v6-v7-peer-audit-v1/metadata-read-failure-v1.json)，纠正后核验 OS0。此条只证明当前 25 案与正常七案业务红绿；后续 cover27、正常11案、完整既有 S→X confirm 及 S 真实供应商轮次须另记实际结果，不能声明角色全阶段或完整迁移通过。


### 2026-10-11 S、T、U 与当前产品回归终态

本节补录已经收回的结果，保留此前原件与失败。三轮均复用冻结 J/v7 标准和提示词；视频与完整 R10/M0–M5 仍未通过。

| 轮次 | 实际结果 | 已达与未达范围 |
| --- | --- | --- |
| [S 原执行](../../backend/.runtime/agnes-storyboard-media-broker-live-s-v7-1440-execution.json) | OS1，pytest Exit / 1 warning，213.04s | 两图真实 POST200、1024²、READY/attached，几何内容仅 limited；三个视频 POST503 队列满，无供应商 ID 或产物。late ACK expected22→stored23；5 tasks / 5 records / 2 attached，无 active/unknown。 |
| [T 原执行](../../backend/.runtime/agnes-storyboard-media-broker-live-t-v7-1440-execution.json) | OS1，54.09s | authorRows 的 GET `/tasks` 列表遇同期 autosave 2→3，返回 `canvas_binding_changed` 409；没有提交、任务、供应商调用或产物。 |
| [U 原执行](../../backend/.runtime/agnes-storyboard-media-broker-live-u-v7-1440-execution.json) | OS1，pytest Exit / 1 warning，208.40s | 两图真实 POST200、READY/attached，活动及终态 cookies-only 恢复、原字节回读完成；三个视频均 POST503 `provider_queue_full`、`accepted_unknown=false`，无视频 ID、GET、下载或产物。唯一原 UI retry 未观察真实 running，第二视频 retry 未执行。 |

U 的 late 原生 ACK 为 expected23→stored24，没有复用 early ACK；reconciliation 为 5 tasks / 5 records / 2 attached，active/unknown/read_errors 为空。七个个体 GET `/tasks/{id}` 返回 409，原响应 body 未保存，不能直接断定错误码；这与 T 的列表失败分开记录。原失败链为 `verify-agnes-storyboard-media-python-v7.mjs:618 → eventually:71 → recoverFirstAcceptedRetry:616 → retryOriginalKnownRejections:764 → runStage:984 → entry:1049`。视频及完整成功 Python、成员权限、duplicate/stale 后置未执行。

Root 实看 U 两张原 PNG 与九张浏览器截图：白桌上的红立方体和蓝实体圆柱，未见文字、标识、人物或额外道具，只有该几何小场 `passed_limited`；浏览器 `partial`，整轮 `not_passed`。见[内容复核](../../.runtime/u-content-review-root-v1/content-verdict.json)。[S](../../.runtime/agnes-storyboard-media-s-v7-peer-audit-v1/final-readonly-receipt-v1.json)、[T](../../.runtime/agnes-storyboard-media-t-v7-peer-audit-v1/final-readonly-receipt-v1.json)、[U](../../.runtime/u-real-failure-review-v1/final-readonly-receipt-v2.json) 独立清理核验及 [Root U 资源回查](../../.runtime/u-real-failure-review-v1/root-final-resource-receipt.json)均有原收据；U 队列、交换机、桶已不存在，15 原服务身份、两源及产品保持。初始 Node/Vite/Chrome 精确身份未存，只证明相应端口消失，不扩大为完整进程身份证明。

此前七文件当前读、角色共享发布及角色接线已实际应用。[前端验证汇总](../../.runtime/beeftv-character-current-ui-applied-verification-root-v1/summary-v1.json)为专项 26、宿主 219、画布 468、浏览器 4 均通过且无跳过，source-check 和统一 build 通过；浏览器 4 使用 API 替身。[正常 MySQL/MinIO 13 案](../../.runtime/beeftv-character-rr-normal13-owned-v2/execution-6bb645f31c3a4b48bcdd47082e118206/execution.json)实际 13 passed / 0 skipped / 20.40s、内外 OS0；首轮漏传 `private=False` 的测试调用 TypeError 原件保留，仅修正测试调用。

任务列表修复已应用：同范围纯 row_version 竞争只重做一次完整事务，重新核对权限、绑定与全项目锁顺序；持续竞争仍 409，来源变化不重试。相关 118 passed / 2 warnings / 150.49s；[完整 unit/API 终态](../../backend/.runtime/task-list-revision-retry-full-29f1f743596444c9be1f7cd598fcaece/return.json)为 2812 passed / 0 skipped / 3 warnings / 731.82s、内外 OS0。全仓 Ruff check 与受影响 Python format 通过；全仓 format 唯一既有范围外失败仍为 `scripts/apply_assembly_migration.py`，未改该脚本。

[当前产品原 27 案](../../.runtime/beeftv-character-27-current-owned-v1/execution-467d2fd90a174e03a1dc12ddb1b21b24/execution.json)为 1 failed / 26 passed / 65.44s。唯一旧断言要求纯 autosave 竞争的 list 返回 409，与新合同相反；不能记成原 27 通过。保持原探针字节、精确排除此旧合同案后，[26 案](../../.runtime/beeftv-character-26-current-owned-v2/execution-20f6a75dcca64190a4b9f9c5da46ed3b/execution.json)实际 26 passed / 1 deselected / 0 skipped / 54.31s。[永久正常入口五案](../../.runtime/beeftv-task-list-rr-normal-owned-v1/execution-b741778b95e946ff8d866b3d108b9ff6/execution.json)为 5 passed / 0 skipped / 17.96s、内外 OS0，真实 conftest/当前 src、无 overlay，随机库及精确容器清理通过，零模型调用。

仍有两项真实产品红，修复与后续验收另记：

- [角色真实浏览器 v3](../../.runtime/beeftv-character-real-ui-candidate-v3/execution-769cdb1188a4463c8f9cae62f9e10bc7/execution.json)在 1440/600 两宽均通过原三菜单、37×19 PNG normalize/copy/保存、cookies-only 恢复和成员 API 隔离，最终 2 failed / 0 skipped / 140.59s。成员共享图保存的是复制资源，DOM `img.src` 却切回来源资源；栈为 `originalDetail:134 → member:258`。供应商 POST0、external0，精确清理通过，最终 DB/MinIO 后置尚未执行；不放宽 URI 断言。
- [公开 HTTP/RR detail v4](../../.runtime/beeftv-task-detail-rr-http-candidate-v4/execution-218da83a30644a1c826bba391ba71f21/execution.json)实际 1 failed / 0 skipped / 8.76s、OS1。真实 MySQL P_S 显示 detail 连接12等待 autosave 连接11的项目 X 锁；POST commit200、revision2→3 后 GET detail409，完整 body 为 `canvas_binding_changed` / “加锁期间画布已变化，请重新读取”。fresh GET200，任务、消息版本和归档结果未变。正常集成入口、注册/登录 cookies 与 ASGI HTTP 路由真实运行，数据库已完成文本是夹具，零模型/对象 I/O；不是 TCP 浏览器或供应商验收。随机测试库 DROP、容器与62752清理通过。

含 current 角色分镜仍在供应商调用前 422，最小接通与冻结版本回填校验在准备；V 仅有准备包、尚未执行。完整迁移继续 active，34 历史 unknown、不获批准的 R23 候选及用户原服务保持；收费、真实音频与企业授权暂缓。


## 2026-10-11 成员封面修复与任务详情正常回归

本节追加此前 S/T/U、角色浏览器 v3 与详情 v4 的失败之后，旧记录和原证据保留。

成员同版本角色重新物化时，现在保留图中已保存的规范项目封面资源 URI，避免重新选择来源项目表示槽而使成员封面失效；新版本仍重新解析。该修复涉及 `canvas-character-reference.ts`、对应画布测试及 `source-adaptations.json` 三文件。[Root 前端验证收据](../../.runtime/member-cover-root-verification-v1/receipt.json)记录画布 470、宿主 219 和 API 替身浏览器 4 项全部通过且无跳过，统一 build 包含两包类型检查并通过，既有 chunk 警告保留；这些 Node/build 工具终态未另存完整日志，不补称有独立日志。

Root 在当前产品正常入口重跑[真实角色浏览器 v4](../../.runtime/beeftv-character-real-ui-candidate-v4/execution-87f0f9d1b7874fc88df1649ce7509887/execution.json)，1440/600 两宽实际 **2 passed / 0 skipped / 1 warning / 162.65s，内外 OS0**，真实 MySQL、MinIO 与账号 cookie，模型调用 0。原三菜单、角色复制保存、跨账号读取及 cookie-only 恢复完成；每宽六个精确来源项目旧 GET404 是声明的允许项，其余异常为空，不能记成全部 HTTP0。Root 实看成员两宽与作者重开详情三张原截图，仅确认 37×19 紫色夹具图片和桌面分栏/窄屏堆叠；这不证明三视图形象质量或完整固定源一比一迁移。[v4 独立只读审核](../../.runtime/beeftv-character-ui-v4-and-maintenance-peer-audit-v1/final-readonly-receipt-v1.json) SHA `35f0ae3b3e4c5eef1aa67095a3e43e54c580ba36da1ccd2e635130808fa4431c`；精确容器、随机库和六桶清理按保存收据记录。Node/Vite/Chromium 初始 StartTime 未存，关闭方法、数字 PID 和端口终态不能扩大为完整进程身份证明。

任务详情修复已由 Root [实际应用四文件](../../.runtime/beeftv-task-detail-revision-retry-candidate-v1/application-2343f665697c45078fce7119514681cf/application-receipt.json)，应用收据 SHA `39eaa7ad43c5c0747b3feac3288f1f1cd8e9d605560715833e00dacb59f06efd`。`detail()`每轮重读本人 binding 与可信画布，通过现有完整 publication 预检；仅同 project/source 的纯 revision 竞争允许完整回滚后再预检一次，第二次竞争仍409，绑定变化和撤权不纳入此重试。通用 `require_canvas` 与任务 action/log/text 恢复合同保持。API 说明同步区分列表与详情各自重查；候选 SQLite/overlay 的通过不代替下面的正常真实 MySQL 回归。

应用后[正常永久入口 v1 原执行](../../.runtime/beeftv-task-detail-rr-normal-owned-v1/execution-679ee13cd2c044a193da20d88b885fc6/execution.json)仍为 **OS1，1 failed / 0 skipped / 1 warning / 10.01s**：原竞争详情已返回200，但测试410行读取 `result_state` 产生 `KeyError: 'result_state'`，不能把该轮记成通过。Root 只把这一测试字段改为已存在的 DTO 字段 `resultState`，原失败、manifest、日志和 JUnit 原件保留，另建 v2。[正常永久入口 v2 原执行](../../.runtime/beeftv-task-detail-rr-normal-owned-v2/execution-f9fbaa7eb75349fa80870fff8c79bbb6/execution.json)实际 **1 passed / 0 skipped / 1 warning / 8.61s，内外 OS0**（JUnit 8.537s）；[pytest 原日志](../../.runtime/beeftv-task-detail-rr-normal-owned-v2/execution-f9fbaa7eb75349fa80870fff8c79bbb6/pytest.log)和[原 JUnit](../../.runtime/beeftv-task-detail-rr-normal-owned-v2/execution-f9fbaa7eb75349fa80870fff8c79bbb6/junit.xml)保留。warning 为现有 Starlette/AnyIO 弃用提示，没有跳过。

v2 使用当前产品 src、永久 integration 路径自然加载 conftest、真实随机 MySQL 库和公开 ASGI TestClient 路由，没有源码 overlay、TCP 服务器、浏览器、模型或对象 I/O。P_S 确证项目 X 锁 WAIT；合法 autosave POST200 将 revision2推进至3，详情旧事务完整 rollback，重新预检的事务 commit，竞争 GET200/READY、保存正文以及 Task/消息状态和结果计数保持均通过。前置认证、local/render 的部分事务只观测为 active，不能声称全部事务显式提交或回滚。该轮仅证明单次同范围纯版本真实竞争，不扩大为连续竞争、来源变化或撤权的本轮真实 MySQL 验收。[v1/v2 独立观察](../../.runtime/beeftv-task-detail-normal-v1-v2-peer-observation-v1/observation.json) SHA `50efbb44c54f16e569268b800b5dfb2be887c1cc364ea7add8ce1da70838d1b8`；[v2 原库清理](../../.runtime/beeftv-task-detail-rr-normal-owned-v2/execution-f9fbaa7eb75349fa80870fff8c79bbb6/sql-cleanup.json)确认随机库 DROP，原 execution 记录精确容器 owner_verified/removed 和端口终态，本次维护没有再次资源查询。

先前 **2812 passed / 0 skipped** 的完整 unit/API 结果只对应旧任务列表修复基线，不作为新详情或 R10 修改后的全量验证。以上封面和详情验证均零模型调用，不能替代真实供应商视频或完整迁移验收；S/T/U 未通过、34 条历史 unknown 和其他原失败保持。本次维护只核既有文件与链接，没有执行测试、模型或基础设施操作。


<a id="r10-v-final-audit"></a>

## 2026-10-11 R10 当前角色正常回归与 V-v7-v2 失败结案

本节以已经产生的应用、执行和只读收据为准。旧正文及候选 README/manifest 中的“尚未应用”“仅准备”“V 尚未执行”保留为当时快照；本追加不覆写旧轮结果。含角色真实文本 v2 已在准入前失败，W 和下一 v3 在本候选准备时仍仅准备，不能提前记为通过。

### R10 已应用的范围与实际验证

Root [R10 应用收据](../../.runtime/r10-current-role-candidate-v1/application-2bcb1e9dc26b45c7958f7776738bd58a/application.json) SHA `4be422f2ee539e3ffaf4bfe8114dc4c9d7cc1c50c48a1476dcf9d8c325c6c6cb` 对应[候选清单](../../.runtime/r10-current-role-candidate-v1/manifest.json) SHA `b997d5f28e351a8baadd2a4defd2cd4abd289ceffe88f2deaaa22275f5eb508c`，实际应用四个 Service 文件及两份永久测试。分镜冻结真实角色身份、版本和 definition；同一角色重复卡可保留，同名不同身份拒绝。create/prepare/approve 经正常 publication 校验并沿用来源/归属项目锁顺序；任务回填按本人冻结 binding 再校验，删卡、刷新或只推进 authoritative current 后不能回填旧角色任务。没有 ORM/schema/DDL 改动。`canvas_generation_service.py` 只在 `create_locked` 增加一处准入 hunk，已修详情的 `def detail()` 至 EOF 原字节保持，实际尾部 SHA 为 `20e508dab31de7d79541646d1e3e14543828e0dfa05a6df86d46b701cfd51177`。

| 实际入口 | 结果 | 证据与限制 |
| --- | --- | --- |
| 当前产品角色分镜、既有分镜及详情定向 unit | OS0，42 passed / 0 skipped / 2 warnings，59.02s | [原 return](../../backend/.runtime/detail-r10-related-d9436a39522a4e65a95eeac27eabbf63/return.json)与[JUnit](../../backend/.runtime/detail-r10-related-d9436a39522a4e65a95eeac27eabbf63/junit.xml)；非真实供应商 |
| 当前 `tests/unit tests/api` | OS0，2834 passed / 0 skipped / 3 warnings，818.32s | [原 return](../../backend/.runtime/detail-r10-full-487b7a406d1c4891a262bd336ee68028/return.json)与[JUnit](../../backend/.runtime/detail-r10-full-487b7a406d1c4891a262bd336ee68028/junit.xml)；输入前后相符，早于下述九行 integration 夹具追加 |
| 全仓 Ruff check | OS0，`All checks passed!` | [原 return](../../backend/.runtime/detail-r10-ruff-663070310c5a49f5b2f0ae48d384affb/return.json)，范围 `src tests scripts` |
| 本次相关九个 Python 文件 format check | OS0，`9 files already formatted` | [原 return](../../backend/.runtime/detail-r10-format-4aaa691268c945a8b27047bf2d4bdafe/return.json)；不写成全仓 format 通过 |
| 正常 MySQL/MinIO 28 案 v1 | OS1，20 passed / 1 failed / 0 skipped / 1 warning，259.57s | [原 receipt](../../.runtime/r10-current-role-mysql-v1/receipt.json)、[JUnit](../../.runtime/r10-current-role-mysql-v1/junit.xml)；`-x` 后七案未执行，未完成 28 案 |
| 正常 MySQL/MinIO 28 案 v2 | OS0，28 passed / 0 failures / 0 errors / 0 skipped，1 warning，320.71s | [原 receipt](../../.runtime/r10-current-role-mysql-v2/receipt.json)、[JUnit](../../.runtime/r10-current-role-mysql-v2/junit.xml)；`normal_product_no_overlay`，使用当前 `src`，供应商为受控本地测试服务器 |

正常 v1 的首红为 `test_member_scope_revocation_blocks_approval_without_supplier[chat-completion-source]`。成员取本人 document 后尚未保存本人 `stylePresetId`，在工具创建前置处即失败，尚未进入原案预期的撤权/批准段。原 [pytest 日志](../../.runtime/r10-current-role-mysql-v1/pytest.log) 与关键 stack 保持：

```text
test_canvas_storyboard_current_character_runtime.py:398
    run, guard, submission = prepare_storyboard(scope)
test_canvas_storyboard_runtime.py:198
    assert created.status_code == 201, created.text
AssertionError: {"error":{"code":"canvas_tool_source_changed","message":"请先设置并保存项目画风"}}
assert 409 == 201
```

复现入口为原 v1 `run.py` 冻结的正常 integration 测试文件、`-q -x --tb=short`；它执行当前产品，不使用 overlay。共享 allowlist 包含 `content`，不包含 `stylePresetId`；画风 preset 等参数归本人，不能把共享作品可见当作成员已经配置私有参数。v2 的[九行精确夹具补丁](../../.runtime/r10-current-role-mysql-v2/fixture.patch)让成员通过正常 commit 保存本人 preset 与 content，再发原请求；没有放宽产品门禁。Root [夹具应用收据](../../.runtime/r10-current-role-mysql-v2/fixture-root-application.json)记录 integration 文件从 `adce3818f66a0c93e5f64732a9828100d6a4f42b56de9d2fc8e57d0c036e4a15` 变为 `25e6d05b38e7ba0f407baec2f76d1e9ea96fa188c8f4e9d140aa885adc72f28e`，仅加九行、保留 LF 和 v1 原失败。正常入口 v2 随后实际完成全部 28 案；这是 MySQL/MinIO、公开 ASGI 路由和受控本地供应商验证，不能称 TCP 浏览器或 Agnes 验收。

normal-v2 原日志只明确 `Task-owned MySQL cleaned: beeftv-resume-mysql-test-a9de157051fe49929a33f06d620aa503`；本追加没有独立查询全部桶是否不存在，不能把这一 wrapper 清理记录泛化成所有资源的独立存在审计。[全量与夹具应用只读观察](../../.runtime/r10-application-normal28-peer-audit-v1/full-and-fixture-application-observation.json)中的 normal28 “pending”仍是其生成时快照，v2 终态以新 receipt/日志/JUnit 为准。[normal28-v2 独立终态复核](../../.runtime/r10-normal28-v2-and-live-v2-peer-audit-v1/observation.json) SHA `e854c3864cec0eec237b882d15b224222534e1cd7d879de3d95023bdff21f84f` 确认四个成员 source/owning × 两协议撤权案实际 passed。本维护候选不重复测试或资源查询。

### 含角色真实文本 v2 在准入前失败

随后独立 socket、正式栈的 rolelive-v2 已实际执行，[原 receipt](../../.runtime/r10-current-role-live-v2/receipt.json) SHA `cd031a81394fd695120562ae22d9f9f0d6cb3e250fe09aa22fccc829be298e51` 与[JUnit](../../.runtime/r10-current-role-live-v2/junit.xml)为 **OS1 / 1 failed / 0 skipped / 16 warnings / 87.67s**。停止在原添加角色菜单等待 normalize 响应之前；没有生成任务或供应商请求，不能写成 Agnes 失败，也没有真实文本内容通过。关键 stack：

```text
page.waitForResponse: Timeout 30000ms exceeded while waiting for event "response"
  at addCurrentCharacter (.../verify-agnes-storyboard-current-character-python.mjs:369:32)
  at .../verify-agnes-storyboard-current-character-python.mjs:417:11
test_canvas_agnes_storyboard_current_character_broker_live.py:427
  pytest.fail: Formal storyboard failed before admission
```

Root 和独立 peer 实看[原 failure PNG](../../.runtime/r10-current-role-live-v2/live-temp/test_current_character_storybo0/browser/failure.png)：弹窗内阿青已选，底部红提示“画布或角色读取上下文已变化，请重新读取”，角色尚未插入。初始加载的真实 viewport PUT 和 document commit 从 revision2 推进到3，和目录读取冻结的 revision2 上下文竞争；保留陈旧上下文拒绝 guard。源角色 socket create/upload/confirm/catalog 已发生，但封面是37×19紫色静态夹具，不是图片模型内容验收。未发生 normalize/copy 或角色卡commit，`resource_posts/run_posts/prepare_posts/approve_posts/execute_posts/task_posts` 全为0；原 reconciliation `task_states=[]`、task/run ID null，provider report/body/SSE 均无。完整权限、冻结请求、执行、回填、内容及 broker 后置尚未到达。

[Root 只读截图及输入复核](../../.runtime/r10-current-role-live-v2-root-review-v1/review.json) SHA `bcdacc1be192d05c3947bb3ff35d54221d2393b3d881ac57fe4047bd9e6191ce` 与[独立终态观察](../../.runtime/r10-current-role-live-v2-peer-terminal-v1/terminal-observation.json) SHA `b196568d5a1d94a614b5681fa283449949c4947c3ecdd4e04a65cde259ffd5dc` 保留原失败和输入。原stack自报八角色/queue cleanup，wrapper记精确MySQL cleaned；[独立精确存在观察](../../.runtime/r10-current-role-live-v2-peer-terminal-v1/cleanup-readonly-observation.json)仅核八个记录数值PID当前不存在、4215/58010/51742无监听、精确名容器 `No such container` 且精确名过滤为空。初始StartTime／运行容器ID未保存，未独立查询bucket或broker不存在，不扩大为完整身份证明或所有资源清理。

### V-v7-v2 原执行终态与内容失败

V-v7-v2 用独立 4213、来源键、log/temp/baseline 重复冻结 J/v7，三份源码、原几何标准和提示词保持。原 [execution](../../backend/.runtime/agnes-storyboard-media-broker-live-v-v7-v2-1440-execution.json)为 **OS1 / pytest Exit / 1 warning / 210.71s**；这是异常分支 `pytest.exit`，不改写成 `1 failed`。真实原因在原 browser report：唯一红 retry 已终态 failed，无法观察真实 running；Babel 500KB note 只是 stderr 前缀。关键 stack 为 `verify-agnes-storyboard-media-python-v7.mjs:618` → `eventually:71` → `recoverFirstAcceptedRetry:616` → `retryOriginalKnownRejections:764` → `runStage:984` → 主流程 `1049`。

两图真实 POST200，分别保存 1024² 原 PNG，READY/attached，并完成图片活动及终态的仅 cookie 恢复。Root 实看两张供应商原图与全部九张原 browser PNG，[独立内容收据](../../.runtime/v-v7-content-review-root-v1/content-verdict.json) SHA `c49e1100fa4de99521d46943f08dc2661dcd55c93cffd3d5247f9c713a8e15a4`：红立方体只在该小场 `passed_limited`；蓝圆柱左上角出现部分出画的额外白色柱状物，违反“只有一个纯蓝色圆柱／无其他物体”，**内容 failed**。浏览器 `partial`，视频 `not_passed`，整轮 **`not_passed`**；不沿用旧轮“两图 limited”的结论。

| V 原本人任务 | 真实供应商结果 | 受理与产物 |
| --- | --- | --- |
| 红图 `367628966640304128` | POST200，802432 bytes，SHA `f6ef936f651ddce39b92f58bf22255f4fcdedee689ba59d7d01d2e179b9211c2` | PNG；仅内容 limited |
| 蓝图 `367628957077291008` | POST200，825829 bytes，SHA `dcd092cac67ff1a1f0b1d85ca3e3d2cae31d24e2f45aaae65aa394697f105364` | PNG；内容 failed |
| 原红视频 `367629182034591744` | POST503，`provider_queue_full` | `accepted_unknown=false`，无 supplier ID、GET、下载或产物 |
| 原蓝视频 `367629191287226368` | POST429，`provider_rate_limit` | `accepted_unknown=false`，无 supplier ID、GET、下载或产物 |
| 唯一原 UI 红 retry `367629518266777600` | POST503，`provider_queue_full` | `accepted_unknown=false`；原 `retryOf`/`attemptGroupId` 保持，无产物 |

原 UI 首红 retry 只点击一次，严格串行；距参考原 POST 完成已过 65003ms，实际等待 58132ms，点击时距参考约 67167ms。首 retry 无真实 running 即停止，第二原蓝视频 retry 未执行。video 原早 ACK `20→21` 的请求仍含 running/queued；其后实际收到新原生 ACK `21→22`、stored graph revision22，两视频均 failed、无内容，saved 可见、live/stored viewport 一致，`reused_matching_early_ack=false`。本轮 `page_errors/http_errors/trace_errors=[]`，旧详情 GET409 没有出现在本轮错误清单；这不等于供应商没有 429/503，也不构成整轮通过。

原 v7 异常分支已自然 reconciliation：5 tasks/5 records/2 attached、API/DB 五个 ID 对齐，active/unknown/read_errors 均空，观察时八角色仍 alive，`natural_terminal_observed=true`；没有额外 POST/retry/resume/injection 或重复后置。视频真实 running 关闭/恢复、成功重试回填、终态恢复、媒体定位/字节/播放器/时间推进与内容、重复防提交、完整成功成员私人访问及 duplicate/stale AMQP 后置均未完整执行；本轮不是含角色或文本模型验收。

[V 独立最终只读收据](../../.runtime/v-v7-v2-readonly-audit-v1/final-readonly-receipt-v1.json) SHA `6e71c90e53f48a8aba3d22e45befd8ca413657d2888adc02999bcdde09e13362` 核 25 文本证据、34 temp 原件在两终态窗口逐项同 SHA，写收据时再次重哈希相符；launcher/snapshot/三 v7 源不变。27 产品匹配只限已记录窗口及 Root 实际回查时点；后来获授权的 R10 integration 九行夹具变动属于新当前文件，不能回写 V baseline 或改变 V 历史失败。独立当前存在核确认 11 个已记录 PID 不存在、59087/50893/4213 无监听、精确隔离 MySQL 不存在；[Root 精确 AMQP/MinIO 回查](../../.runtime/v-real-failure-review-v1/root-final-resource-receipt.json) SHA `5aebdfa0c5bccddfb9d431869afb9fceef11473892154aeb6d04b5001a653c14` 实际核 11 queues/2 exchanges/3 buckets 不存在，15 原服务 StartTime 及两源 HEAD/clean 保持。本审计未读 WMI/CIM/CommandLine 或凭据；未保存完整 Node/Chrome 前置 PID/StartTime 身份，因此端口消失不能扩大为完整历史进程身份审计。V 资源清理不改变内容或供应商失败。

### 后续准备的边界

[W-v7 v1 清单](../../.runtime/agnes-storyboard-media-w-v7-preparation-v1/manifest.json) SHA `4e0f48c72a42a555982813715380e4a41243b2426680b915115930d05cb8ad94` 仅准备独立 4214、证据路径和后续新 snapshot；PowerShell 两脚本 parser OS0、0 errors，44 份 V 保护原件 SHA 保持，未执行 snapshot/基础设施/供应商。W 要求新 snapshot 冻结全部 27 当前产品、原 15 PID/StartTime、两源；原 v7 每轮新项目/document.id 生成独立 sourceKey，标准、提示词和串行原 UI 重试 guard 保持。[含角色真实文本 v2 原准备清单](../../.runtime/r10-current-role-live-v2/manifest.json)及[准备检查](../../.runtime/r10-current-role-live-v2/final-v5-preparation-receipt.json)仍保留准备时事实，后续真实 v2 失败以上节为准。F 的下一 live-v3 仅准备等待真实初始 autosave／新上下文稳定后再打开角色菜单，尚未执行；不放宽产品 guard、不预建副本、不静默重投。Root 后续真实结果另节记录。

上述局部修复及 28 正常集成通过不等于完整 R10/M0–M5 或一比一迁移完成。A–V 旧失败、蓝图内容失败、34 历史 unknown 及未批准候选保持；本追加零新测试、供应商调用、服务启动及资源操作，收费／真实音频供应商／企业授权仍按原范围暂缓。


<a id="r10-role-v3-v4-current"></a>

## 2026-10-11 接续：角色文本 v3/v4 与提示词修复

角色真实文本 v3 已实际调用一次 `agnes-3.0-flash`，HTTP200、完整 SSE，但两镜的 `assetBindings` 元素返回字符串，严格 schema 在 `rows.0.assetBindings.0`、`rows.1.assetBindings.0` 以 `model_type` 拒绝，任务 `367639365670486016` 为 `invalid_storyboard_result`。`characters:["阿青"]` 本来正确。实际为 **OS1 / pytest.exit / JUnit tests0、failures0、errors0、skipped0 / 1 warning / 212.09s**，不能记为一案通过或一案 failed。原 resource_posts12 只有合计与一次已知归一化响应，没有12条明细，不能事后断言全部 normalize。[原件诊断](../../.runtime/r10-current-role-live-v3-diagnosis-v1/observation.json) SHA `6e689578c6d696e587a4d077c939c02b88967fd23a2f004ed6b55cdd18ff2e96` 保留供应商原文、请求和严格拒绝；[独立清理回查](../../.runtime/r10-current-role-live-v3-cleanup-audit-v1/observation.json)确认已记录PID、精确MySQL及11队列/2exchange不存在，原桶名未记录，未独立回查旧桶。

正式修改仅 `backend/src/short_drama/service/canvas_storyboard.py` 的 system prompt 与 `backend/tests/unit/test_canvas_storyboard_generation.py`：明确角色名字符串数组，以及绑定对象的 `nodeId/role/priority`、合法角色枚举、0–100整数、既有节点与不重复约束，并给合法JSON示例；严格 schema/parser 未改，未修补供应商返回。新增六案先红 `StopIteration`（1 failed / 25 passed），补提示词后相关两模块 **40 passed / 36.26s**。全仓 Ruff check、两修改文件 format check 实际OS0；[独立提示词核查](../../.runtime/r10-current-role-binding-prompt-peer-review-v1/observation.json)确认非prompt AST保持。

当前产品完整 unit/API 使用仓库独占 `--basetemp` 实际 **2840 passed / 0 failures / 0 errors / 0 skipped / 2 warnings / 811.66s，内外OS0**，见[执行收据](../../.runtime/r10-binding-prompt-validation-v1/unit-api-v2.json)、[原日志](../../.runtime/r10-binding-prompt-validation-v1/unit-api-v2.log)和[JUnit](../../.runtime/r10-binding-prompt-validation-v1/unit-api-v2.xml)。首次默认Temp因 `PermissionError: [WinError 5] ...Temp/pytest-of-snow` 未完成，原件保持，只停止精确己方pytest父子进程后另轮重跑；不将首次执行记通过。2840属于无真实模型的后端验证，不替代真实MySQL/供应商验收。

角色文本 v4 使用新独占库、三桶与真实栈，实际 **OS1 / pytest.exit / JUnit tests0、failures0、errors0、skipped0 / 1 warning / 158.35s**。真实Agnes仅POST1、HTTP200、160102字节SSE、885 delta events、731 callback chunks、DONE/stop，任务 `367647404364480512` succeeded。请求 SHA `976aec923e6fb5dbcded7aecdbe96d0a2f93f6b54e2bc707ebfc5c10e29714e2`，SSE SHA `47726b167370141a72c6547a51ccbab8a3c4f315778091a3a29723caec210629`；[原执行收据](../../.runtime/r10-current-role-live-v4/receipt.json)和[原浏览器报告](../../.runtime/r10-current-role-live-v4/live-temp/test_current_character_storybo0/browser/report.json)保持。两镜绑定为正确对象；原消费者回填、72段终文重放、关闭后稳定恢复、成员共享与私人任务404检查已到达。完整Python成功后置及duplicate/stale AMQP未执行，整轮仍 `not_passed`。

v4失败复现：按原添加角色卡菜单、原分镜Ctrl+Enter生成，真实终文回填后关闭恢复，再以成员登录查看共享分镜；完整资源检查于 `resource-tracing.mjs:89` → driver:747 抛 `AssertionError [ERR_ASSERTION]: actual {} != expected {367647342771126272:[367647144091312128]}`。19请求/19响应均POST normalize200，首次source→唯一copy，后续copy→copy；1–17作者 `367647126236160000` aliases完整，18–19成员 `367647127184072704` aliases为空。DAO `copy_ancestors` 仅读取该Actor可见复制来源，`db/access.py` 将copy_sources/upload限定本人；永久回归 `test_collaborator_can_save_published_work_without_authors_private_copy_receipt` 已在2840内通过。此处是harness把作者私人复制祖先错误要求成员可见，产品权限无需放宽。下一v5只准备可信登录/cookie身份逐Actor的严格断言：作者完整、成员为空、未知身份拒绝，保留映射、唯一副本、全部响应与其他写入拒绝及成功后置；截至本次追加尚未运行新供应商任务。

Root实际阅读全文JSON，并看生成、restored、member三张原PNG：仅固定“阿青右手持红信封、看门后左手敲门、两镜各5秒”内容与对应编辑器显示为 `passed_limited`；正文、调度和图像/视频提示词连续一致。见[Root有限内容收据](../../.runtime/r10-current-role-live-v4-root-content-review-v1/content-verdict.json) SHA `5cbc13575c3f3e52e581615b155c94acac6796ed4cf69e79dfa332400c23b6ed`。这不证明真实形象图片、视频、窄屏、自然供应商中断或完整迁移。v4原fixture实际remove返回三精确桶deleted=true，wrapper记录精确task-owned MySQL cleaned；独立终态资源回查另行记录，不能把原返回泛化为独立absence。

来源项目404的后端v2与前端适配仍为隔离候选，未应用；后端候选39个SQLite/ASGI定向测试通过，尚未真实MySQL/浏览器。W-v7仍仅准备，未snapshot、运行或供应商调用，须在文本稳定后Root新冻结当前产品再启动。旧V蓝图内容失败和视频503/429/503保持；完整R10/M0–M5、真实视频、公网首帧、七角色同版部署及一比一迁移未完成，34历史unknown不重投，收费/独立真实音频/企业授权继续暂缓。本次文档追加不触发新测试、模型或服务操作。


<a id="r10-role-v5-w-terminal"></a>

## 2026-10-11 接续：角色真实文本 v5 通过；W 图片成功但整轮未通过

角色 v5 正常真实执行为 **1 passed / 0 skipped / 1 warning / 139.26s，内外 OS0**。仅一次 `agnes-3.0-flash` POST200，完整 SSE（177363 bytes、982 content delta、814 callback chunks、DONE/stop），正常消息及两个旧消息仍只有一次供应商调用。作者完整 aliases、成员空 aliases 按真实登录/cookie 的 Actor 逐请求核验，未放宽产品权限；完整数据库、归档、来源保持、权限及 duplicate/stale AMQP 后置均执行。Root 实看全文及三张原图，仅两镜阿青右手持红信封、先看门再左手敲门的小场内容为 `passed_limited`；37×19 紫色角色夹具不能证明角色形象图片质量，未验自然供应商中途断流或其他入口。

v5 [原执行收据](../../.runtime/r10-current-role-live-v5/receipt.json)、[Root 内容结论](../../.runtime/r10-current-role-live-v5-root-content-review-v1/content-verdict.json)与[独立精确清理回查](../../.runtime/r10-current-role-live-v5-cleanup-audit-v1/observation.json)分别保留。最后回查 OS0，原任务 PID/children、精确 11 队列/2 交换机、独占 MySQL 容器及三桶均不存在；首次清理辅助脚本错误假设成功轮有 reconciliation 的 OS1 原件保留，新包纠正后只读，不重跑模型。

W/v7 实际 **OS1 / pytest.exit / 1 warning / 87.66s**，不能记为 1 failed 或通过。首图片已经 succeeded、次图片仍 running，验收 driver 第897行只允许 active 或视频明确未受理，触发 `1 !== 2`。复现：冻结 v7 →真实选第1/3行图片生成→持久化前关闭快照→自然图片早完成触发断言。后续全空 content 与 active/failed-only 断言同样需在新独立 harness 处理 mixed 状态；旧 v7、旧请求和证据不改、不重投。

W 真实图片为两个 `agnes-image-2.5-flash` POST200，最终两任务及 records 都 succeeded/READY，但两个结果 **detached**；完整成功 Python/成员/AMQP 后置与视频阶段未执行。视频供应商调用 **0**，不能写成视频供应商失败。Root 实看两张原 1024² PNG 及失败截图：白桌红立方体和蓝实体圆柱各只有一个主体，无人物/文字/额外物体，限定几何小场内容 `passed_limited`；截图不证明最终回填。任务 ID 分别 `367656021000011776`、`367656044651692032`，原图 SHA 分别 `a84f5ddc46546304d4500099626867e9998a29e8bc69e00f67b2bfec0c622503`、`25b25b63be76ba7fcf0518740b254520877d460f37ffbdcc2d7c1327c08e2994`。

W [原执行](../../backend/.runtime/agnes-storyboard-media-broker-live-w-v7-v1-1440-execution.json)、[原自然终态](../../backend/.runtime/agnes-storyboard-media-broker-live-w-v7-v1-1440-temp/test_original_table_geometry_m0/reconciliation.json)、[内容收据](../../.runtime/w-v7-root-content-review-v1/content-verdict.json)及[精确资源回查](../../.runtime/w-v7-root-final-resource-review-v1/receipt.json)保留。资源回查实际 OS0，零写操作、零应用/供应商请求；记录的任务 PID、11队列/2交换机、三桶、精确 MySQL 名称与55651监听均不存在，27当时产品 SHA、15原服务 StartTime 和两源保持。MySQL 名称/端口取实际日志，缺初始完整 ID/StartTime，不能扩大成完整历史身份。

来源404修复的后端7、前端12及接口说明已 byte-exact 应用，另已应用永久真实 HTTP/RR 测试；当前真实验证另轮执行中，[应用收据](../../.runtime/source-project-read-apply-root-v1/application.json)不代表验证通过。没有 ORM/DDL/依赖变化。完整 R10/M0–M5、真实视频、公网首帧、七角色同版部署与一比一迁移仍未完成，goal active；旧失败及34历史unknown保留，收费、独立真实音频、企业授权暂缓。


<a id="r10-source-browser-x-v8-current"></a>

## 2026-10-11 接续：来源两宽链通过；X 两图回填但视频未受理

来源404修复的后端7、前端12及接口说明、永久HTTP/RR与浏览器测试已应用。正常产品专项 **39 passed / 0 skipped / OS0**，完整unit/API **2879 passed / 0 skipped / OS0**；39是完整集合子集，不相加。全仓Ruff check OS0，全仓format **OS1**仅原范围外 `scripts/apply_assembly_migration.py:16/23` 混合换行，`1 file would be reformatted, 826 files already formatted`；原字节和失败收据保持，外层runner为OS1，不称全部后端检查通过。见[正式后端收据](../../.runtime/source-project-read-applied-backend-validation-v1/receipt.json)及[格式原日志](../../.runtime/source-project-read-applied-backend-validation-v1/ruff-format.log)。

永久来源HTTP/RR v2在真实隔离MySQL与loopback TCP正常产品实际 **12 passed / 0 skipped / 1 warning / 41.96s，内外OS0**，模型和MinIO调用0，精确己方MySQL清理已按原收据确认。见[真实HTTP/RR原收据](../../.runtime/source-project-read-integration-candidate-v2/evidence/run-1783b0bcb3794ed9aef548cd4ab7b892/receipt.json)。章节就近反馈六文件已byte-exact应用；[正式前端验证](../../.runtime/source-project-feedback-applied-frontend-validation-v1/validation.json)为宿主219、画布516共 **735个唯一全量Node测试通过、0skip**，专项81是重复子集。source check为832文件/217 adaptations/91 removed；统一build **125.140s/OS0**，含两包类型检查、构建与画布复制，保留一条chunk warning。没有ORM/DDL/依赖变化。

来源browser历史六轮保持原终态，修正只发生在新driver/wrapper，未改旧原件或正式产品断言：

| 真实轮 | 原终态与实际停止点 |
| --- | --- |
| wrapper-v1/run-4ab4c9… | 1 failed/0skip，缺Chromium可执行文件，浏览器未创建；原server关闭/精确容器清理保留 |
| wrapper-v1/run-e34da0… | 1 failed/0skip，等待全局隐藏toast超时；missing后DB断言未到达 |
| wrapper-v2/driver-v3 | 1 failed/0skip，真实ACK200/版本1→2；离线正式投影揭示JSON.stringify键序误拒，但旧轮缺完整my-document回读，不能写“仅键序” |
| wrapper-v3/driver-v4 | 1 failed/0skip，仅1440；33完整my-document回读、3原生ACK、完整nodes/connections语义相等，随后同位节点遮挡导致dblclick超时，409/600未到 |
| wrapper-v4/driver-v5 | 1 failed/0skip/54.92s，仅1440；44完整回读/4ACK、原UI编辑保存草稿、两真实409；预览区两个alert导致strict locator失败，第二409后完整图/刷新/恢复/600未完成 |
| wrapper-v5/driver-v6 | 1 passed/1 failed/0skip/130.98s，内外OS1；1440 complete，600完整cookie图已相等，但收尾点击隐藏fit按钮超时，最终恢复检查尚未完成 |

对应[最早两轮只读核证](../../.runtime/source-project-read-browser-failure-peer-review-v1/observation.json)、[driver-v3诊断](../../.runtime/source-project-read-browser-failure-review-v3/receipt.json)、[v4原件核证](../../.runtime/source-project-read-browser-failure-review-v4/receipt.json)、[v5核证](../../.runtime/source-project-read-browser-failure-review-v5/receipt.json)与[v6两宽失败核证](../../.runtime/source-project-read-browser-failure-review-v6/receipt.json)保留。peer纯离线核证通过不改变原浏览器失败；这些轮模型与MinIO调用0。

新wrapper-v6/driver-v7的[正常真实原执行](../../.runtime/source-project-read-browser-integration-candidate-v6/evidence/run-71d0df057f244cb88bd6ef2bd9873058/receipt.json)为 **2 passed / 0 skipped / 1 warning / 93.56s，内外OS0**。两宽分别complete：1440实际20次来源请求、58完整my-document回读、5原生ACK、最终4节点、精确2次409，覆盖literal/空/missing预览、plus、真实drag、首章就近反馈、原UI编辑并保存草稿、409后完整图与草稿保持、明确刷新重选和cookies-only恢复；600按源隐藏sidebar与fit入口，实际6次来源请求、18完整回读、1ACK、最终1节点、精确1次首章409，验证错误就近可见且空图不插入，显式刷新版本后原文字面连续保持、首章插入与cookies-only恢复。600不写桌面sidebar/plus/drag已验，未保存本地编辑保护不由已保存草稿案证明。

Root实看本轮1440草稿/409和600空图409/恢复literal三张原截图；[独立完整原件peer](../../.runtime/source-project-read-browser-evidence-review-v6/peer-run-71d0df057f244cb88bd6ef2bd9873058/receipt.json) SHA `37043836beef0a2af6428057b9c23ad445f0430f3a1693e71d9e07f08a8db57e`核两宽全部原断言、原生保存、真实读取与身份范围通过，未重跑测试、模型或资源。模型、生成与MinIO调用0；浏览器和己方Vite关闭、精确隔离MySQL删除按原执行清理收据为据，不扩大为全服务独立absence，也不称供应商或完整源视觉验收。

X/v8-v2采用新当前49产品身份，Root实际采样与freeze_baseline均OS0；[本轮存储身份](../../backend/.runtime/agnes-storyboard-media-x-v8-root-1440-before-identity-v1.json) SHA `817764527398f850337835fb5426d2b86bd47dd5046b3f2b587bb24217a70819`与[新基线](../../.runtime/agnes-storyboard-media-x-v8-preparation-v2/product-baseline.json)一致，原15服务StartTime与两源HEAD/clean保持。[真实原执行](../../.runtime/agnes-storyboard-media-x-v8-preparation-v2/receipt.json)实际 **OS1/pytest.exit/JUnit tests0、failures0、errors0、skipped0/1warning/220.03s**，整轮 **not_passed**；不是1failed，也不能用零JUnit失败称通过。

两次 `agnes-image-2.5-flash` POST200最终succeeded/READY/**attached**，原消费者回填、原字节、活动与终态cookies-only恢复均已到达，与旧W两图detached不同。红/蓝任务分别 `367683736679825408`/`367683747199139840`，1024²原PNG分别809940/797438 bytes，SHA分别 `983fb996c66b56866dccd1180ed1f5a2813ce257139adff27e923c8385f8e21d`/`326cdcda4dfe2387482017f410fe9c55d93e932e91d065e51187a5200c48a5b2`。Root已实际查看两份原supplier-inline-1.bin，仅白桌单红立方体/单蓝实体圆柱、无额外物体或可见文字，小场内容 **passed_limited**；维护准备者未自行视觉检查，不扩大为角色形象图片质量。

原红视频 `367683990015787008`、原蓝视频 `367683997049634816`、唯一原UI红retry `367684323119022080` 分别真实 **POST503/429/503**，全部明确未受理，无供应商任务ID或产物。原视频明确未受理阶段有新的原生ACK/revision24，`reused_matching_early_ack=false`，仅证明该阶段而不是最终失败后保存。首红retry未观察到真实running即停止，蓝retry未执行；复现为正常原Table生成两图→真实回填与恢复→生成两视频→按原失败控件唯一红retry，driver-v8 `recoverFirstAcceptedRetry:622` 报 `首个重试未能观察到真实 running，停止`，经620/768/1091/1156回传。视频恢复/播放/内容、完整成功Python/成员权限/duplicate-stale AMQP后置未执行。原[浏览器报告](../../.runtime/agnes-storyboard-media-x-v8-preparation-v2/live-temp/test_original_table_geometry_m0/browser/report.json)和[自然reconciliation](../../.runtime/agnes-storyboard-media-x-v8-preparation-v2/live-temp/test_original_table_geometry_m0/reconciliation.json)保持：5tasks/5records/2attached，active/unknown/read_errors空，无额外重投。

[独立终态peer-v2](../../.runtime/agnes-storyboard-media-x-v8-terminal-review-v2/receipt.json) SHA `8fb7471538d9ac551a307f91caee95d239937d4a1f8148b360c17687b34c010e`确认只有23个成功原生ACK，最高revision27；第24次提交只有request、无记录response，失败retry后的最新graph持久化 **未证明通过**，不能以revision24或27代替。peer-v1曾把已增加生成imageNodeId的选中行要求与原行全等而误拒，v2只构造两个选中行该原关联字段的完整预期对象，其余原字段/未选行严格保持；v1原件保留。两图已attached/bind applied和已到达恢复的事实不由晚保存缺口抹消。Root已审并批准冻结[有限内容收据](../../.runtime/agnes-storyboard-media-x-v8-root-content-review-candidate-v1/content-verdict.json)，Root观察归属、原SHA/大小、未验视频/完整成功后置及晚保存边界均明确；内容整理者没有自行视觉验收、模型或资源调用。

Root显式运行新[存活身份capture](../../.runtime/agnes-storyboard-media-x-v8-root-runtime-identity-v1/observation.json) OS0，SHA `27de5281d46cade93b9cfb3f18a2d9dfa8e5c668d51eabbd467f7b042b5cc816`；八角色、worker children、Node/Chrome所在pytest子树共36个实际进程身份及full容器ID在存活快照记录。原runner/fixture完成后，[只读终态回查](../../.runtime/agnes-storyboard-media-x-v8-root-final-resource-review-v1/receipt.json)实际OS0，SHA `89a458171ae25be93668a362bcdab84b30de3f7259d5cdc788ffda93d7ce5501`，结论 `passed_recorded_live_snapshot_scope`；实际已捕获36个PID/StartTime、精确MySQL ID/name/62259及4214/53925本轮端口、11queue/2exchange/三桶均无残留，原15服务/49产品/两源与原证据前后保持。只支持本次存活快照和原报告身份，不证明未来新进程、脱离pytest子树或wrapper祖先；清理通过不改变供应商整轮not_passed。

终态回查helper-v2的[冻结manifest](../../.runtime/agnes-storyboard-media-x-v8-terminal-readback-preparation-v2/manifest.json) SHA `676e570c851d1d6a434c3e6c758ef68dbc42bceec5b178ef24b8efe93062e2d3`保持四核心脚本与v1原字节，新增真实函数全mock shape三项绿及旧update构造红OS1、原13合同通过，均不是实际资源验收。[独立helper peer](../../.runtime/agnes-storyboard-media-x-v8-terminal-readback-peer-v2/receipt.json) SHA `af81e7acb47d08582450d5b7822985809ed99ccc1d3d2246946bf0740c970a1b`核35guards/name→docker_name→collector串联无静态阻断。实际capture与终态资源回查另按原收据记录，不由准备通过推断。

普通Agnes视频[r390无印刷纸盒输入](2026-10-11-beeftv-agnes-video-content-broker-audit.md)已真实 **1 passed/0skip/613.22s/OS0**；[本机HEVC原上传/首段播放](2026-10-11-beeftv-hevc-browser-audit.md)已在Chrome154、1440/390 **2 passed/0skip/84.92s/OS0**。普通视频与分镜输入不同，HEVC来自本地派生，不消除旧内容/供应商失败，也不证明供应商输出H.265或所有codec/浏览器。W两图成功但detached、视频调用0的原结论保持。

R22新有限1000节点/3round以同机固定源生产包与当前统一dist实际执行，18组手势完成，[原report](../../.runtime/r22-1000-source-target-preparation-v3/evidence/run-1000-current-a/browser/report.json)状态 **frame_budget_failed**、`frame_budget_1000_passed=false`、profile=false，32ms预算未调整。target三轮drag p95为16.8/16.8/16.8ms、pan为50.0/66.6/50.1ms、zoom为50.0/50.1/50.0ms；源较慢不代表目标达预算。Root工具终态OS1，但[原receipt](../../.runtime/r22-1000-source-target-preparation-v3/evidence/run-1000-current-a/receipt.json)的 `benchmark_os_exit_code=null` **如实保留**，不把工具退出码写成缺失的子退出码；before/after guard通过、本轮产品修改0/供应商0。v1/v2启动前guard失败保持。随后target-only profile已完成，仅[诊断summary](../../.runtime/r22-target-profile-current-v1/diagnostic-summary.json)的 `diagnostic_only=true`，不作为32ms或优化已通过的证明；当前仍定位，未实施优化。本场为生产浏览器/受控HTTP夹具，不验MySQL、MinIO、AMQP、真实模型、GPU呈现帧或完整泄漏。

[有限门禁矩阵](../../.runtime/beeftv-current-gate-decision-matrix-v2/README.md)所列三个具体任务中来源两宽链已闭合，X本轮分镜视频与新R22的32ms仍未通过；后续仅沿同机1000节点/3round诊断→最小修复→非profile复验，不要求无限组合。同版部署、可信公网首帧与R23未批准源固有修复另列；完整R10/M0–M5和一比一迁移仍未完成，34 historical unknown和全部旧失败保留，收费、独立真实音频供应商与企业授权暂缓。本维护整理不重复模型、浏览器、服务或资源查询，旧“来源验证执行中”是当时历史快照。


<a id="r10-y-v8-terminal-current"></a>

## 2026-10-11 接续：Y 红视频已生成；蓝视频唯一重试未受理，整轮未通过

Y/v8-v1 使用新独占候选、4216 浏览器端口和新当前产品基线；验收输入、原 Table 操作及每个明确未受理原任务至多一次的串行 UI 重试标准保持。Root 新采样的[运行前身份](../../backend/.runtime/agnes-storyboard-media-y-v8-root-1440-before-identity-v1.json)与[冻结基线](../../.runtime/agnes-storyboard-media-y-v8-preparation-v1/product-baseline.json)绑定当前49产品、15个原服务 StartTime 及两固定源，正式产品未改。[真实原执行](../../.runtime/agnes-storyboard-media-y-v8-preparation-v1/receipt.json)实际 **OS1 / pytest.exit / JUnit tests0、failures0、errors0、skipped0 / 1 warning / 586.85s，not_passed**；不记为1failed，也不能用零JUnit失败称通过。原[日志](../../.runtime/agnes-storyboard-media-y-v8-preparation-v1/live.log)、[浏览器报告](../../.runtime/agnes-storyboard-media-y-v8-preparation-v1/live-temp/test_original_table_geometry_m0/browser/report.json)和[自然终态](../../.runtime/agnes-storyboard-media-y-v8-preparation-v1/live-temp/test_original_table_geometry_m0/reconciliation.json)保持。

| 原请求或唯一界面重试 | 实际供应商结果与已到达范围 |
| --- | --- |
| 红图 `367691494703579136` | `agnes-image-2.5-flash` 单 POST200，succeeded/READY；原PNG 779850 bytes，1024² |
| 蓝图 `367691503658418176` | 同模型单 POST200，succeeded/READY；原PNG 815731 bytes，1024² |
| 红视频 `367691707233157120` | `agnes-video-2.5-flash` 单 POST200，供应商ID `task_HETktyzf4duWpv8YIEocskeic1smVZiC`；14次真实 GET200 至 completed，原下载570434 bytes，DB/API succeeded/READY |
| 蓝视频原请求 `367691714703212544` | POST429 / provider_rate_limit，明确未受理，无供应商ID、输出或下载 |
| 蓝视频唯一原 UI retry `367693596377038848` | POST503 / provider_queue_full，明确未受理，无供应商ID、输出或下载；立即停止后续点击 |

两张图原体 SHA 分别为 `3403261b4bbeec57b3e984aa21615d38ca2738e2735db5603319d5b030ce1ec7`、`b762a9b70fb3edd7cda9888060e4d28a42d25f70a75f4ea4449e4896bf42f6f4`。供应商体与浏览器读取输出逐字节一致，原消费器 bind200、节点回填、真实活动 cookies-only 恢复、终态 cookies-only 恢复及原 Table 终态重复点击不新增逻辑任务/批次均到达。两个选中行仅增加对应 imageNodeId，全部其余原字段及未选择的钥匙对照行保持；不将图阶段通过扩大为整轮成员权限或消息重放后置。

红视频原下载 SHA 为 `5d0b8d80c8e3b61fdc507a999aea0f8aa2b3f8ce3379426d1f3d49afe0b87a28`。关窗前红任务真实 running，蓝原任务 failed；新原生 ACK/revision22 明确保存这组状态及原视口，cookies-only 重开后同一原任务、节点、batch、完整原行与关联恢复。红任务后来 READY，API/节点元数据为1280×720、4480ms、570434 bytes；**global** 原消费器 bind200/applied/bound，原节点成功及稳定 `resource:` 定位已记录。`stage.video.bindings` 尚空，因为失败退出前未到阶段 finishTraces 归组；不能据此抹消完整 global 原请求/响应，也不能补造视频阶段输出获取或播放记录。

Root 已实际查看两份原 supplier-inline-1.bin、红视频九点 contact sheet 及第0/4/8采样原尺寸帧。[内容收据](../../.runtime/agnes-storyboard-media-y-v8-root-content-review-v1/content-verdict.json) SHA `30175cdc09d96a83720caeec98edb830e91c70e9fece208961256665ba24f24d` 与[采样收据](../../.runtime/agnes-storyboard-media-y-v8-root-content-review-v1/sampling-receipt.json) SHA `a86140d3ff4bbf7be5237b5d62058dbb18d4871f1bcddc680d4362829598380f` 均保持：白桌单红立方体、单蓝实体圆柱图片及红立方体转动视频的几何小场内容 **passed_limited**，无可见文字、标签、标识、人物或额外桌面物体。红视频镜头与木条背景稳定；原提示词未禁止背景，不追加纯白背景条件。原视频完整解码实际OS0，ffprobe时长4.458333s，请求为4s。九点采样不等于逐帧认证，不认证音频、精确转速、人物形象或其他输入；原浏览器播放、视频输出逐字节获取和视频终态恢复未到。内容整理者未自行视觉验收。

复现为原 Table 第1/3行生成两图→成功回填与活动/终态恢复→原 Table 两视频→原红真实running/蓝429时关窗并仅cookies恢复→等待红成功附着→蓝原失败控件唯一重试→503后严格停止。原关键 stack 为 driver-v8 `772:20` 的 `该镜唯一 retry 再次失败，立即停止后续点击`，经 `eventually:75:13`、`retryOriginalKnownRejections:769:23`、`runStage:1091:19` 回传。冷却按最新原POST完成时刻核438459ms，超过原65000ms；没有放宽重试次数或注入running。5tasks/5records均自然terminal，active/unknown/read_errors为空，额外POST/retry/resume为0。

[独立完整终态peer](../../.runtime/agnes-storyboard-media-y-v8-terminal-review-v1/receipt.json) SHA `de5a683f1a34334f46f745319c57059d61335ca86a232b68e99fabb05f37ef17` 实际OS0、317项证据核对通过，**overall_supplier_acceptance=false**。41次原生commit请求只有40次200 ACK，最后证明 **revision45**：红视频 `succeeded/content_present=true`，蓝retry仍 `queued/content_present=false`。第41次请求写入蓝retry failed但没有记录response/ACK，**最终蓝failed图持久化未证**；这不抹消红成功的已确认保存。视频阶段 outputs/media_views及final_snapshot为空/未到，两视频全READY、终态cookies恢复、原 Table终态重复无新增、成员共享作品/私人任务与模型权限、HTTP重放与相同/旧message_version的AMQP防重复调用、完整MySQL/MinIO媒体后置及acceptance.json均未执行。正常Worker递送image4/video18仅证明正常执行，不当重复递送验收。

Root 首次从仓库根执行 capture，被 `must_run_from_backend` 在资源查询前阻止；[v1失败原件](../../.runtime/agnes-storyboard-media-y-v8-root-runtime-identity-v1/observation.json) SHA `371418b5bc31242f3feb8ae2ec2e186b3fc7a7761f6823f705959ae653116de2` / OS1保持。随后从backend显式运行[v2存活捕获](../../.runtime/agnes-storyboard-media-y-v8-root-runtime-identity-v2/observation.json)实际OS0，SHA `3071e06874d81c191d37dbb8017f4176410b09ecb9acd731e4286f03f61e9f13`；终态collector显式绑定成功v2，未覆写v1或使用默认失败记录。[只读终态回查](../../.runtime/agnes-storyboard-media-y-v8-root-final-resource-review-v1/receipt.json)实际OS0，SHA `e4fecbdb5ac1847c4f9a87a6db124d520ff293915dce35dc020752aea9cdcf86`，仅结论 `passed_recorded_live_snapshot_scope`。36个实际已捕PID/StartTime/parent身份均absent，八角色和Worker子进程捕获完整；独占MySQL `beeftv-resume-mysql-test-b3808b85307c45beb96a0f166d03ee69` 完整ID `20bb11010da32462fa8f46c4a63837d36752cd71c3d3a9e01094dccb2d48a176`及4216/49738/61360本轮监听、该轮UUID AMQP命名和三个随机测试桶均精确清理。原15服务/当前49产品/两固定源及原证据前后保持。只覆盖捕获时点和该轮原报告身份，不证明未来新进程、脱离pytest子树或外层wrapper祖先；不把清理通过写成供应商整轮通过。冻结helper的[独立静态peer](../../.runtime/agnes-storyboard-media-y-v8-terminal-readback-peer-v1/receipt.json)核11候选/41引用guards及三核心允许替换、逆bytes/AST一致，只属准备核对，不代替实际capture/collector。

R22 后续仅作两条件 Dock 隔离注入诊断：magnification禁用或pointer缺席时跳过geometry读取。Root直接tool session31993终态为OS0，**没有额外wrapper收据**；[原report](../../.runtime/r22-target-dock-inactive-diagnostic-v1/report.json)为1000节点、target-only单round，`status=diagnostic_profile`、已启用profile（`profile_enabled=true`）、`frame_budget_1000_passed=null`。[注入记录](../../.runtime/r22-target-dock-inactive-diagnostic-v1/injected-diagnostic-only.json)的 `served=1` 证明该诊断包实际送达，`diagnostic_only=true/product_mutations=0/acceptance_budget_result_valid=false`。drag/pan/zoom p95分别16.8/49.9/33.5ms；这是profile诊断，不能记正式预算通过或产品优化已应用。此前同机源/target生产包1000×3round、18组的 `frame_budget_failed` 与receipt子退出码null仍保留，32ms门禁不变；正式产品未改，仍需最小候选审查、必要修复后的同场非profile复验。

本次维护仅追加证据与状态，不重复模型、浏览器、资源或既有全量测试。来源两宽链已闭合的结果保持；Y分镜媒体整轮与R22正式预算仍未通过。完整R10/M0–M5、一比一迁移、同版部署和可信公网首帧仍开放；34历史unknown及A–X旧失败原件保持、不重投，收费、独立真实音频和企业授权继续暂缓。


<a id="r10-z-v8-v2-terminal-current"></a>

## 2026-10-12 接续：Z-v2仅修验收时长口径，真实视频未受理

Z/v8-v2 的必要修正只发生在新独占验收 harness：`probe_video` 从视频流时长优先改为容器 `format.duration` 优先，缺失时回退 `stream.duration`，并增加一条 `math.isfinite(duration)`。真实产品 `probe_resource` 原本使用容器时长，产品代码未改。Root 对同一 Y 归档原视频的[实际离线复现](../../.runtime/agnes-storyboard-media-v8-duration-root-reproduction-v1.json)确认原 probe 为4458ms、实际产品为4480ms，22ms差触发原5ms断言；[新实际离线红绿收据](../../.runtime/agnes-storyboard-media-z-v8-preparation-v2/duration-checks.json)用同一视频和真实生产函数证明新 probe 为4480ms、差0ms。三次本地 ffprobe 不是新供应商调用；八个内存时长边界明确为测试替身，不扩大为供应商验收。

原324条断言逐项保留，仅增加finite一条，总325；原 `<=5ms`、请求时长 `-200..500ms`、输出与记录 `<=500ms`、每个明确未受理原任务至多一次的串行原 UI retry、未观察running即停止及unknown不重投均保持。除这两个必要时长语义变更，其余核心逆替换后原字节和含属性Python AST相等；driver234个assert成员调用及独立Acorn AST保持，十个未改核心文件逐字节一致。完整test仅撤销轮次配置仍不byte-exact，不能声称所有源完全未改。[独立纯静态peer](../../.runtime/agnes-storyboard-media-z-v8-static-peer-v2/receipt.json)实际OS0、468项核对通过，只核准备包/guards/原件，不改变Y原失败，也不作为Z真实通过。

Root为Z-v2重新执行身份采样与freeze，均OS0；[新身份](../../backend/.runtime/agnes-storyboard-media-z-v8-root-1440-before-identity-v2.json) SHA `2c89bb4154acaa892a33e0b598ae0965907ffc530d26e2c0e94d2cd558ff4df6` 与[新基线](../../.runtime/agnes-storyboard-media-z-v8-preparation-v2/product-baseline.json) SHA `3a99d2edbebb589f2ad948953579e571def751ac1d07fa8259948fe0f8e548ff`绑定当前49产品、15原服务StartTime和两固定源，未复制Z-v1旧基线；相对Z-v1产品与服务记录无差，两源clean。新run UUID、4218端口与独占缓存路径均绑定冻结manifest。[真实原执行](../../.runtime/agnes-storyboard-media-z-v8-preparation-v2/receipt.json)实际 **OS1 / pytest.exit / JUnit tests0、failures0、errors0、skipped0 / 1 warning / 223.46s，not_passed**；不是1failed，不能用零JUnit失败称通过。时长后置尚未到达，不能称本轮已真实验过修正后的媒体时长。

| 原请求或唯一界面重试 | 实际供应商结果 |
| --- | --- |
| 红图 `367704351440715776` | `agnes-image-2.5-flash` POST200，succeeded/READY；原PNG 800218 bytes |
| 蓝图 `367704361960030208` | 同模型POST200，succeeded/READY；原PNG 819104 bytes |
| 红视频原请求 `367704581250826240` | `agnes-video-2.5-flash` POST503 / provider_queue_full，明确未受理 |
| 蓝视频原请求 `367704588578275328` | 同模型POST429 / provider_rate_limit，明确未受理 |
| 唯一原UI红retry `367704930321776640` | POST503 / provider_queue_full，明确未受理，立即严格停止 |

三个视频请求均无供应商任务ID、输出或视频产物，蓝retry未执行，无额外重投。正常原Table选两镜→生成并回填两图→活动与终态cookies-only恢复→两原视频失败→唯一原UI红retry，driver `recoverFirstAcceptedRetry:622` 抛出“首个重试未能观察到真实running，停止”；原[浏览器报告](../../.runtime/agnes-storyboard-media-z-v8-preparation-v2/live-temp/test_original_table_geometry_m0/browser/report.json)与[自然终态](../../.runtime/agnes-storyboard-media-z-v8-preparation-v2/live-temp/test_original_table_geometry_m0/reconciliation.json)保持，5tasks/5records自然terminal，active/unknown/read_errors为空。两图已到达的原字节、原消费者绑定回填和恢复事实保留。

本轮原生 **22次commit / 22个HTTP200 ACK**，无已记录未ACK提交；最高 **revision26**（expected25，`2026-10-11T16:08:14.939Z`）明确保存红retry `367704930321776640` failed/retryCount1/content=false、原蓝视频failed/retryCount0/content=false，两图succeeded/content=true。[F独立早停终态peer](../../.runtime/agnes-storyboard-media-z-v8-early-terminal-review-v1/receipt.json) SHA `52077cb861a5f5442a94eb6489e23d86c7afdd92b9ce6ad90b44e25a81309129`实际 **OS0 / 223项核对通过 / 25条原件guard前后保持**，只确认原证据，整体供应商验收false。该次完整请求与原200响应证明已保存的失败图，不由截图或早ACK代替；它仍不是失败后的新cookies-only视频终态恢复。本轮没有Y的第41次晚提交无ACK缺口，旧Y缺口原结论保持。缺本轮capture，要求capture的基础终态peer-v3未执行，没有补造存活身份。

Root实际查看两份供应商原PNG及原failure截图；红图SHA `764bec7c46b045f38020117fb10bfad4c0d2f85156978827710008bd8346da39`、蓝图SHA `e02bf6af684d60999d96642a45638ffd8ab6a5140d768af47fec436680356d92`与[Root有限内容收据](../../.runtime/agnes-storyboard-media-z-v8-root-content-review-v1/content-verdict.json) SHA `6c8cd644e852241b001b6b0fe8e42f228029127ab38d66c960509ee9ee85cd46`一致。仅白桌单红立方体、单蓝实体圆柱的几何小场 **passed_limited**，无可见文字、标志、人物或额外主体；维护准备者未自行视觉验收。视频内容无产物可验，视频恢复/播放、完整媒体Python、成员共享/私人权限、HTTP和duplicate-stale AMQP后置均未到；正常Worker递送不能当重复消息验收。

Root真实[有限资源回查](../../.runtime/agnes-storyboard-media-z-v8-root-final-resource-review-v2/receipt.json) **OS0**，SHA `680d838bd50465b1d8074482aae23eeb252939e87d2c76ecac5a16cc9bbb1cc7`，结论 `passed_limited_recorded_pid_name_scope`。缺本轮存活capture，因此只证明报告中的10个PID当前不存在、原日志精确MySQL name/port65213不存在、4218/65213/65296监听为空、本轮UUID的11queue/2exchange及三随机桶无残留，原15服务/49产品/两源与证据前后保持。该回查在下述Dock产品应用前完成；没有初始PID StartTime或MySQL fullID，不扩大为36进程快照、完整历史容器身份、未来新进程或wrapper祖先，清理通过不改变供应商整轮not_passed。

R22实际FloatingDock组件核对的v1–v4旧真实失败原件保持，各自old模式cases0：依次为浏览器导出launch错误、夹具GET404、组件转换500、等待页面load超时。v2重复baseURL仅为观察，不认定为唯一阻塞原因；v4加载到一份原组件也不证明交互案例通过。新[v5真实组件report](../../.runtime/r22-dock-inactive-component-preparation-v5/executions/run-bc68218f-1ccf-4538-85a8-c04bcee7096e/report.json)已 **OS0 / status=passed**，old与candidate各24个实际挂载组件cases完成；独立审核另行核，不预写其结果。**该组件验证场**产品修改0、供应商0，`r22BudgetResultValid=false/fullPageVisualAcceptance=false/knownRangeZeroCenterCovered=false`，不代表后续产品没有应用，也不证明正式32ms预算或range0中心边界通过。

Root随后真实应用Dock两条件提前返回，仅修改 `frontend/canvas/src/components/ui/aceternity/floating-dock.tsx` 和 `frontend/canvas/source-adaptations.json`；[两文件应用收据](../../.runtime/r22-dock-product-application-candidate-v1/applications/run-caa1f6c7db9c4509b97e152ca1be6ca1/receipt.json) SHA `fca619e7aa1c488bf5267d709040de22f42a7949e4191a4a24e08e644dc12045`，状态applied，实际OS0。[正式应用后验证](../../.runtime/r22-dock-applied-verification-v1/receipt.json) SHA `84bdfa1c3b71a029cf3575b96ef6e0fa1db94f32b80305fda6e05921543113fc`实际 **canvas全量516 passed / 0 skipped / OS0（7.266576s）**；source检查 **832文件 / 218 adaptations / 91 removed / OS0（0.391993s）**；统一build **OS0 / 130.059486s**，包含两包类型检查、生产构建和画布复制，保留一条chunk warning。[构建原日志](../../.runtime/r22-dock-applied-verification-v1/unified-build.log) SHA `58ada4eeef45f1c8a0d8573337eabf167c1e0c3628fb9c97035335aeb153765e`保持。没有ORM/DDL/依赖变化；本维护准备者未重跑组件、测试或构建。

应用后新[v4正式性能原执行](../../.runtime/r22-1000-source-target-preparation-v4/evidence/run-dock-1000-after-a/receipt.json) SHA `227d5754a5fc5471b2a311f7de5a1bac7577e59ec593a8acf71eb008b33cda5b`已实际 **OS1**：before guard0、benchmark真实子退出码1、after guard0；同机固定源与当前目标生产包、非profile、1000节点各3round，18组手势完成。[原report](../../.runtime/r22-1000-source-target-preparation-v4/evidence/run-dock-1000-after-a/browser/report.json) SHA `aadddea09bf43a991726d705252c9a5f73a64f3b44ddf9cdb9f9cb1127b6ee5e`完整 `frame_budget_failed/frame_budget_1000_passed=false`，目标三轮drag p95均16.8ms，zoom为49.9/50.0/49.9ms，32ms预算不变。两条件修复未证明正式性能改善；本轮供应商0、产品修改0，仅生产浏览器/受控HTTP夹具范围，不认证真实供应商、GPU呈现帧或完整泄漏。旧正式receipt的子退出码null保持，新实际1不是从report推断；range0中心未覆盖、全页视觉未验仍保留，不预判下一性能或其他验收。

来源两宽既有有限通过、普通r390视频和本机HEVC有限通过保持；Z分镜媒体整轮、R22正式性能、同版部署/可信公网首帧、R23未批准行为及完整R10/M0–M5/一比一迁移仍开放。全部旧失败与34历史unknown保留不重投，收费/独立真实音频供应商/企业授权继续暂缓。本次维护只读整理，不新增模型、浏览器、服务、资源查询或既有全量测试。


<a id="r10-aa-v8-v1-terminal-current"></a>

## 2026-10-12 接续：AA 两图恢复到达，视频未受理且最后失败图未获 ACK

AA/v8-v1沿冻结Z-v2只更换轮次、4220端口与独占缓存，325断言、format优先及原5ms容差、严格串行每原件一次UI retry、unknown不重投保持。[静态独立审核](../../.runtime/agnes-storyboard-media-aa-v8-static-peer-v1/receipt.json)实际OS0、334项核对通过，仅证明准备包。Root以新身份/基线与Dock补充guard执行[本轮真实原run](../../.runtime/agnes-storyboard-media-aa-v8-preparation-v1/receipt.json)：**OS1 / pytest.exit / JUnit tests0、failures0、errors0、skipped0 / 1 warning / 225.00s / not_passed**；零JUnit失败不能当整轮通过。

| 原任务或唯一原界面重试 | 真实供应商结果 |
| --- | --- |
| 红图 `367716267101863936` | `agnes-image-2.5-flash` POST200，succeeded/READY/attached；1024²原PNG 828255 bytes |
| 蓝图 `367716278011248640` | 同模型POST200，succeeded/READY/attached；1024²原PNG 821573 bytes |
| 原红视频 `367716502440067072` | `agnes-video-2.5-flash` POST503 / provider_queue_full，明确未受理 |
| 原蓝视频 `367716509738156032` | 同模型POST429 / provider_rate_limit，明确未受理 |
| 唯一原UI红retry `367716859912208384` | POST503 / provider_queue_full，明确未受理；未观察到running即停止 |

原Table选两镜→两图回填及活动/终态cookies-only恢复→两原视频被拒→原红唯一UI retry→严格停止，driver `622:16` 的关键栈为“首个重试未能观察到真实 running，停止”，经 `eventually:75:13`、`recoverFirstAcceptedRetry:620:20`、`retryOriginalKnownRejections:768:9` 回传。[本轮浏览器原件](../../.runtime/agnes-storyboard-media-aa-v8-preparation-v1/live-temp/test_original_table_geometry_m0/browser/report.json)与[自然终态](../../.runtime/agnes-storyboard-media-aa-v8-preparation-v1/live-temp/test_original_table_geometry_m0/reconciliation.json)证明5tasks/5records自然terminal，active/unknown/read_errors为空；三视频请求无供应商ID或产物，蓝retry及额外重投为0。冷却从原POST完成起65015ms，原65000ms门禁保持。

**本轮24次原生commit只有23个HTTP200 ACK；最高revision27（expected26，响应`2026-10-11T16:55:35.231Z`）仍保存红retry queued/retryCount1/content=false、原蓝failed/content=false。** 第24次请求（expected27，`16:55:37.004Z`）携带红retry failed，但没有记录response/ACK，最终红retry failed图持久化未证；截图和自然任务terminal不能补足该缺口。两图succeeded/content=true的已确认保存保持。[F独立终态审核](../../.runtime/agnes-storyboard-media-aa-v8-early-terminal-review-v1/receipt.json) SHA `3f8b139da00b1c1af4a7716d015eebc5ed92d4efd15b910bd80fb65252281182`实际OS0、275项核对通过、总33条原件及补充文件guard前后保持，整轮false；本轮ACK缺口不回改Z-v2的22/22结果。

两图原体SHA为 `38e6894a8eee94f680f4ac6d2de8b31ef6deffce65175860386f3fd1d7e41105` 与 `511213690a26c2d3ba6d4f44cd2dfc99f49d10ef31f1df767dc1517270d4286b`，原浏览器completed与fresh-terminal读取均匹配，原消费者绑定和活动/终态cookies-only恢复到达。Root实际查看两份原PNG及failure截图，[内容收据](../../.runtime/agnes-storyboard-media-aa-v8-root-content-review-v1/content-verdict.json) SHA `7a83a61de15074a1dbfaf6f30e5c60e68b4410a013a8d81ea794a61bff1352f7`仅确认白桌单红立方体/单蓝实体圆柱 **passed_limited**，整体false；维护准备者未自行视觉验收。视频播放/时长/终态恢复、完整Python媒体、成员与私人权限、HTTP重放和duplicate-stale AMQP后置均未到，正常递送不当重放验收。

Root实际[capture](../../.runtime/agnes-storyboard-media-aa-v8-root-runtime-identity-v1/observation.json) OS0、SHA `3a75c15243063aeeebc6dbd23625fccbfc237a28b25e17f9597a0aac12009d0c`，捕获36个pytest子树PID/StartTime/parent和八角色/Worker子进程，绑定MySQL完整ID `84b8359cb91eafa960638280734944b08a52bc904d18b315d13094e64e4c4a68`、精确name/port61864。[实际collector](../../.runtime/agnes-storyboard-media-aa-v8-root-final-resource-review-v1/receipt.json) OS0、SHA `2084b2ac7071545c0d357cc3a07981710d4a45109dbf4ebd97e25cf051b38322`仅结论 `passed_recorded_live_snapshot_scope`：已捕36PID不存在，精确容器和本轮监听、UUID11queue/2exchange及三随机桶无残留，原15服务/49产品/两源与原证据前后保持；补充guard另绑定floating-dock与ledger，不称49包含Dock。范围限捕获时点与原报告身份，不证明未来/脱离子树进程或外层wrapper，清理通过不改变not_passed。

AA结束后的R22 `run-dock-target-profile-a`[原诊断收据](../../.runtime/r22-dock-applied-target-diagnostic-preparation-v1/evidence/run-dock-target-profile-a/receipt.json)实际 **OS1**：before0、trace0、parser未执行/null、after0；`run.py:60` 断言要求的invalidation类别不在实际available列表`[]`中，诊断未完成。仅target1000节点单round、profile/invalidation，budget_result=null；已产trace只供离线读取，不预判热点、修复效果或32ms正式通过。

随后新独占[纯离线收据](../../.runtime/r22-dock-target-profile-offline-review-v1/executions/run-7797f2ae102541758e2fb1aad074e04b/receipt.json) SHA `5c6fcff4c86f62162d0cebef39221d6a1282809eaad5115469defb27f769c2c7`记录派生parser实际OS0，限定门禁按实采cat/name/stack、6个唯一标记与data_loss=false确认本轮trace可作离线分析，仅属事件证据。原诊断OS1/available categories=[]/原parser未执行null与profile预算null保持，不预写热点根因或正式改善。

既有失败原件、34历史unknown、R22正式32ms失败与range0中心/全页视觉未验保持；完整R10/M0–M5、一比一迁移、同版部署/可信公网首帧及R23未批准行为仍开放，收费/独立真实音频/企业授权继续暂缓。本次维护只读整理，无新增模型、浏览器、服务、资源查询、产品或既有测试执行。


<a id="r10-ab-v8-v1-terminal-current"></a>

## 2026-10-12 接续：AB 仍未通过，重试自然完成后的最高 ACK 仍保存 queued

AB/v8-v1仅从AA冻结核心派生轮次、4222端口与独占缓存，325断言、format优先、5ms/请求-200..500ms/输出500ms、串行每原件一次UI retry和unknown不重投保持。[独立静态收据](../../.runtime/agnes-storyboard-media-ab-v8-static-peer-v1/receipt.json)实际OS0、296项核对通过，只属准备核对。[本轮真实run](../../.runtime/agnes-storyboard-media-ab-v8-preparation-v1/receipt.json)实际 **OS1 / pytest.exit / JUnit tests0、failures0、errors0、skipped0 / 1 warning / 226.05s / not_passed**（JUnit原time225.513s），不由零JUnit失败推断通过。

本轮按冻结prompt SHA与原shot1红/shot3蓝映射，并发任务ID顺序不作为颜色依据：

| 本轮任务 | 实际供应商结果 |
| --- | --- |
| 红图 `367724364943933440` | `agnes-image-2.5-flash` POST200，succeeded/READY/attached；1024²原PNG 783854 bytes |
| 蓝图 `367724354416230400` | 同模型POST200，succeeded/READY/attached；1024²原PNG 800711 bytes |
| 原红视频 `367724584373141504` | `agnes-video-2.5-flash` POST503 / provider_queue_full，明确未受理 |
| 原蓝视频 `367724592388456448` | 同模型POST429 / provider_rate_limit，明确未受理 |
| 唯一原UI红retry `367724940603768832` | POST503 / provider_queue_full，明确未受理；未观察running即严格停止 |

复现为原Table选两镜→两图回填和活动/终态cookies-only恢复→两原视频被拒→原红唯一UI retry→停止，关键stack为driver `622:16` 的“首个重试未能观察到真实 running，停止”，经 `eventually:75:13`、`recoverFirstAcceptedRetry:620:20`、`retryOriginalKnownRejections:768:9` 回传。[浏览器原件](../../.runtime/agnes-storyboard-media-ab-v8-preparation-v1/live-temp/test_original_table_geometry_m0/browser/report.json)和[自然终态](../../.runtime/agnes-storyboard-media-ab-v8-preparation-v1/live-temp/test_original_table_geometry_m0/reconciliation.json)保持；5tasks/5records/2attached results，active/unknown/read_errors为空，三视频无供应商ID/输出，蓝retry及额外重投为0，原POST后冷却65007ms满足65000ms门禁。

**23次commit / 22个HTTP200 ACK，最高revision26（expected25）仍保存红retry queued/retryCount1/content=false、原蓝failed/content=false。** 该ACK请求started `2026-10-11T17:27:40.494Z`，晚于retry自然completed `17:27:40.336455Z`，但请求内容仍queued；响应received `17:27:41.796Z`不能当作failed状态已保存。第23次请求started `17:27:43.449Z`、expected26携带红retry failed却无response/ACK，最终失败图持久化未证；自然terminal和failure截图不补足保存证据。两图succeeded/content=true的已确认保存保持，本轮数值不套AA。[F独立终态审核](../../.runtime/agnes-storyboard-media-ab-v8-early-terminal-review-v1/receipt.json) SHA `7e34fdaab34602667891482889bf622ebdd24a0a611607e8303ebcb1bfd4f3a6`实际OS0、290项核对通过，截至该审核总33条原件及补充文件guard保持，整体false；后续Zoom的ledger变化另由实际应用收据绑定。

两图原体SHA按红/蓝分别为 `1f2cd83b01cfc04b66395d2737147593023e4bd9016df473255371c3d167009c`、`2080c3feaa11c944e3adde81957e83af3e070233f51a3d7ba8ee00eb0c11ccee`，原浏览器completed/fresh-terminal读取与供应商字节一致，原消费者绑定及活动/终态cookies-only恢复到达。Root实际查看两份原PNG与failure截图，[内容收据](../../.runtime/agnes-storyboard-media-ab-v8-root-content-review-v1/content-verdict.json) SHA `c76fa72b1cc3f164441b9d31910569858c0b2e9737256b3cadb47b03cd39a479`仅白桌单红立方体/单蓝圆柱 **passed_limited**，整体false；整理者未自行视觉验收。视频内容/播放/时长/终态恢复、完整Python媒体、成员与私人权限、HTTP重放及duplicate-stale AMQP后置未到，普通Worker递送不当重复消息验收。

Root本轮[capture](../../.runtime/agnes-storyboard-media-ab-v8-root-runtime-identity-v1/observation.json)实际OS0、SHA `428bed58208991baf9deec051bfcf486b9e2dc00c474e8250d640f7ca377353a`，UTC `17:26:15.784134→17:26:32.545183`、16.761049s实采36个pytest子树PID/StartTime/parent及八角色/Worker子进程，绑定MySQL fullID `f495e70588aade5186f7bd6528e1982745af88351e76ba350107d8917976df82`、精确name/port58309。[collector](../../.runtime/agnes-storyboard-media-ab-v8-root-final-resource-review-v1/receipt.json)实际OS0、SHA `161d42a67a17b326e912915e6d21b409c092690a5b473598a12d3852d2274f26`仅 `passed_recorded_live_snapshot_scope`：已捕36PID、精确容器、本轮3监听、UUID11queue/2exchange和三随机桶无残留，原15服务/49产品/两源及原件前后保持；floating-dock49外、ledger49内另有补充guard，不扩大至未来/脱离子树进程或外层wrapper。清理不改变整轮not_passed。

R22离线将编译帧 `I line111 column24250` 定位到ZoomControls的 `updateScaleDisplay`，trace见文本替换及 `Affected by :has()` 相关栈。[真实挂载组件报告](../../.runtime/r22-zoom-percent-component-preparation-v1/executions/run-69ebddd7-a661-4678-9985-d39a953d9bb4/report.json)中原/候选各23例通过、完整回调与props结果一致，4组相同百分比的文本替换各由40降为0；仅相同字符串跳过label.textContent写入，百分比变化仍赋值。Root已按[实际应用收据](../../.runtime/r22-zoom-percent-product-application-candidate-v1/applications/run-70d0893a542e4e0081e02c3ec3ead7a1/receipt.json)修改Zoom TSX并追加ledger共2文件，sourcechecker实际OS0（832files/219adaptations/91removed）；随后[画布npm test](../../.runtime/r22-zoom-percent-applied-verification-root-v1/canvas-test.log)实际OS0、516passed/0skipped，[统一build](../../.runtime/r22-zoom-percent-applied-verification-root-v1/build-exit.json)实际OS0、128.1620634s，两包类型检查与生产构建完成；[选定视口e2e](../../.runtime/r22-zoom-percent-applied-verification-root-v1/viewport-e2e-exit.json)实际OS0、1passed/0skipped。随后[正式v5重测](../../.runtime/r22-1000-source-target-preparation-v5/evidence/run-zoom-1000-after-a/receipt.json)实际before0/benchmark1/after0，原driver、1000节点/source-target各3轮18组且非profile、32ms门槛保持；target drag三轮p95四舍五入均16.8ms、zoom50.0/49.9/50.0ms，`frame_budget_failed`，未证明正式性能改善。range0中心/全页仍未验，不把组件、build或单条e2e结果当唯一根因、整页一比一或供应商通过。

旧失败、34历史unknown、R22正式32ms失败、range0中心/全页视觉未验保持；完整R10/M0–M5/一比一、同版部署/可信公网首帧、R23未批准行为和本轮媒体后置仍开放，收费/独立真实音频/企业授权暂缓。文档整理者仅生成维护候选，未新增模型/浏览器/API或资源查询、产品修改和测试重跑。


<a id="r22-textdata-v6-current"></a>

## 2026-10-12 接续：Text.data 已应用，正式 v6 仍未达 32 ms

本节只追加前端修正和正式性能复验；既有 AB 真实供应商结论与保存缺口保持。AC-v1 的 snapshot/freeze、AC-v2 的保存观测准备不列入新的真实供应商验收。

Root 已按[两文件应用收据](../../.runtime/r22-zoom-textdata-product-application-candidate-v2/applications/run-39cc228a8fc741c190190576736c2d98/receipt.json)实际应用 Zoom TSX 与既有 ledger 记录更新：保留同百分比跳过，每次从当前 label 重读子节点，唯一 Text 时更新 data，空、多子节点或非 Text 结构回退 textContent；无持久 Text 缓存，liveScaleRef、input 焦点、提交/clamp、菜单、viewport、DOM/CSS 与保存路径保持。Zoom SHA `04525aec…`、ledger `ced685d1…`；219 个登记键、其它记录、顺序、原 CRLF 前缀和 upstream `6c9cd79a…` 保留，实际 source checker OS0（832/219/91）。

[真实组件 v2](../../.runtime/r22-zoom-textdata-component-preparation-v2/executions/run-d82fcff1-87c7-412f-b69f-d8f220f2ad80/report.json)由 Root 实际运行自然 OS0，old/candidate 各 26 例：原 23 个正常路径与全部 callback/props 一致；四组各 40 次同百分比 preview 两版均零 mutation；101/123/100 三个变化值，旧版各一条 childList，新版各一条 characterData、零 childList，当前 Text 身份保持。另三例空/多 Text/元素结构均明确为夹具人为边界，只证明 textContent 回退，不能当正常产品 DOM 或供应商验收。

[应用后前端验证](../../.runtime/r22-zoom-textdata-applied-verification-root-v2/receipt.json)均为 Root 实际 OS0：画布 npm test 516 passed/0 skipped；统一 build 完成两包类型检查、生产构建及画布产物复制，保留 chunk warning；选定“图形视口预览”e2e 1 passed/0 skipped，DPR 1/2、API 夹具。F 只读核对原件，没有重复执行测试。

[正式 v6 收据](../../.runtime/r22-1000-source-target-preparation-v6/evidence/run-textdata-1000-after-a/receipt.json)与[原报告](../../.runtime/r22-1000-source-target-preparation-v6/evidence/run-textdata-1000-after-a/browser/report.json)保持原 driver、1000 节点、source/target 各 3 轮共 18 手势组、非 profile 和 32 ms 门槛。Root 实际 run OS1，before0/benchmark1/after0，`frame_budget_failed`；target p95 四舍五入为：

| 轮次 | drag | zoom |
| --- | ---: | ---: |
| 1 | 16.8 ms | 33.5 ms |
| 2 | 16.8 ms | 50.0 ms |
| 3 | 16.8 ms | 50.3 ms |

原收据 SHA `747a800b…`、报告 SHA `7a0b32fd…`，v5 原失败保持。组件减少文本结构替换不证明消除 :has() 或正式性能改善；本轮是受控 HTTP 浏览器性能验证，不代表 Python/MySQL/MinIO/AMQP、真实供应商、range0 中心/全页视觉或完整一比一验收通过。后续性能和媒体验收仍需各自的新实际原件。


<a id="ac-v8-v3-postcheck-current"></a>

## 2026-10-12 接续：AC 两图两视频成功，Python 重复消息后置仍失败

AC/v8-v3 沿用冻结的白桌几何输入、原 Table 的两镜选择和直出视频路径。Root 实际自然 run **OS1 / JUnit tests1、failures1、errors0、skipped0 / 1 warning / 503.36s / not_passed**（JUnit time503.352s）；[原 run 收据](../../.runtime/agnes-storyboard-media-ac-v8-preparation-v3/receipt.json) SHA `36e94b91…`、[JUnit](../../.runtime/agnes-storyboard-media-ac-v8-preparation-v3/junit.xml)与[完整关键 stack](../../.runtime/agnes-storyboard-media-ac-v8-preparation-v3/live.log)保留。AC-v1/v2 准备不是独立真实通过轮次，本节不改判 AB 或更早失败。

颜色按冻结 prompt SHA 与原 shot1/shot3 匹配，不按并发任务 ID 顺序：

| 本轮任务 | 实际供应商及最终媒体 |
| --- | --- |
| 红图 `367746161798103040` | `agnes-image-2.5-flash` POST200；succeeded/READY/attached，1024² PNG、809010 bytes |
| 蓝图 `367746172518744064` | 同模型 POST200；succeeded/READY/attached，1024² PNG、841281 bytes |
| 原红视频 `367746382485602304` | `agnes-video-2.5-flash` POST200；原供应商轮询 completed，1280×720 MP4、539560 bytes、format4.480s，原请求4s |
| 原蓝视频 `367746389708193792` | POST429/provider_rate_limit，明确未受理；NOT_AVAILABLE、0输出，无供应商 ID |
| 唯一原 UI 蓝 retry `367747268188389376` | POST200；原供应商轮询 completed，1280×720 MP4、379021 bytes、format5.184s，原请求5s |

原红已成功且 attached 后，原 UI 对蓝色明确未受理项只点击一次 retry；冷却门禁保持65000ms，本轮距参考供应商 POST 完成199047ms，无红 retry、额外重投或 unknown 自动重提。两图是2份 inline PNG、0下载；两视频是2份实际下载，原消费者各自绑定200。原格式优先、5ms元数据差/请求时长-200..500ms/输出500ms门禁保持，视频配置仍为无音频的720P/16:9/direct。

[原浏览器报告](../../.runtime/agnes-storyboard-media-ac-v8-preparation-v3/live-temp/test_original_table_geometry_m0/browser/report.json) SHA `df8fa571…` 为 **step=complete、37次commit/37个HTTP200 ACK、最高revision43（expected42）**，page/http/trace errors均空。两阶段均到达原 active cookies-only恢复和 final cookies-only恢复；两图原字节在owner completed/fresh-terminal/member共享三阶段一致，两视频六次媒体读取均实际播放前进，时长分别4.480/5.184s。原取消与重复Table操作没有新任务，未选对照镜无生成目标；成员能看共享作品但私人batch隐藏。28条私人访问记录为owner200×8/member404×8/outside404×12，不由项目权限扩大私人任务、候选或模型权限。本轮ACK43和成功作品只属于AC，不用于补AB的末请求无ACK缺口。

复现路径为原Table选两镜→两图回填/恢复→原红running、原蓝429→红完成并绑定→原UI蓝唯一retry→蓝running/完成/绑定→终态恢复与成员查看→Python发送原版本及旧版本AMQP重复消息。Python完成前置图、两类媒体与原Worker递送检查后，在driver **1283/1284** 的 `eventually → read_json(video-worker-report.json) → pathlib.read_text:1027 → Path.open:1013 → io.open` 抛出 **`PermissionError: [Errno 13] Permission denied`**。后续视频重复消息断言、最终记录/绑定/结果不变性与最终acceptance没有完成，原`acceptance.json`/`reconciliation.json`未产出；本次具体占锁者未知，不能写成已证明的产品或Worker写入缺陷。后来静态读到递送SUCCESS不能替代这次自然失败或完整后置通过。

Root [采样收据](../../.runtime/agnes-storyboard-media-ac-v8-root-content-review-v1/sampling-receipt.json) SHA `84960ffe…` 实际OS0，两原MP4全视频流解码均OS0，分别九点抽帧并保留原体SHA。Root实看两PNG、两九点拼图及各0/4/8原尺寸帧和五张原浏览器截图，[内容v2](../../.runtime/agnes-storyboard-media-ac-v8-root-content-review-v1/content-verdict-v2.json) SHA `92b9ff14da72caa9ff4b29a3a837104598ec7dfcada9079b2f6843e8cd4320a2`仅 **image/video passed_limited、whole_run not_passed**：白桌单红立方体与单蓝实心圆柱，无人物、文字或额外主体；红体朝向变化，蓝背景为黑帘。纯色对称圆柱的九点不能独立认证转速或方向，未验音频，抽帧不代表逐帧或其它输入内容。旧content-verdict.json中文编码损坏原件保留，仅v2作为内容结论；F未自行视觉验收或重跑解码。

Root同轮[capture](../../.runtime/agnes-storyboard-media-ac-v8-root-runtime-identity-v3/observation.json)实际OS0、SHA `3d40ff5e…`，实采36个pytest子树PID/StartTime/parent及八角色/Worker子进程，记录精确MySQL fullID `0cd6bb0a62364ee2d2df5d68bdfa3e06830f41473fb7e6da454e5f1bb7575820`/name/61122端口。原[collector](../../.runtime/agnes-storyboard-media-ac-v8-root-final-resource-review-v3/receipt.json)实际OS1、SHA `4c37e5a5…`，在资源查询前以 `failure_requires_original_reconciliation` 拒绝，保持原件。新的[postcheck collector](../../.runtime/agnes-storyboard-media-ac-v8-postcheck-terminal-readback-root-v1/receipt.json)实际OS0、SHA `13d0603cf0cd85fe10bed18145841cc1e67d812b1cbe8a50fc23ffc358fd2be0`仅 `passed_recorded_live_snapshot_scope`：已捕36PID、精确容器、3监听、同UUID11queue/2exchange与三随机桶无残留，原15服务/49产品/两源及原件前后保持；[补充两组件after](../../.runtime/agnes-storyboard-media-ac-v8-root-supplemental-product-v3/after.json)也保持。它没有补造reconciliation，`whole_acceptance_passed=false`、`business_terminal_proven=false`；未来/脱离子树进程和外层wrapper未覆盖，清理不证明供应商通过或业务最终状态。

[U轻量peer](../../.runtime/agnes-storyboard-media-ac-v8-u-postcheck-peer-v1/receipt.json)实际OS0、SHA `802e4a2a…`，核对10候选/11参考guard、未改函数AST与Root新collector，确认browser/stdout语义一致、complete和37/37完整ACK；本轮失败收尾观测分支未触发，原OS1和业务终态未证明保持。peer没有重执行资源查询、供应商或内容评审，也不扩大清理与内容结论。

下一轮需修复测试观测的共享文件读取路径并用新独占基线实际完成全部后置；AD新harness目前未live，准备不计通过。既有失败与34历史unknown不改判、不自动重投；Text.data是当前产品，正式v6 zoom33.5/50.0/50.3ms未达32ms，range0中心/全页视觉、完整R10/M0–M5/一比一、同版部署/可信公网首帧与R23未批准项继续开放，收费/独立真实音频/企业授权仍暂缓。本次F仅准备文档增量，无正式文档或产品写入、资源/browser/supplier调用及测试矩阵重跑。


<a id="ad-ae-limited-current"></a>

## 2026-10-12 接续：AD/AE 失败图保存到达，整轮仍未通过

AD/v8-v1 的实际结果为 **OS1 / JUnit tests、failures、errors、skipped 全0 / 1 warning / 225.56s / not_passed**；AE/v8-v1 为 **OS1 / 同四项全0 / 1 warning / 211.40s / not_passed**。两轮均得到两张真实 Agnes PNG，未得到供应商视频；原两视频明确未受理后只点击原红镜一次 UI retry，后续严格停止，无蓝镜 retry 或额外重投。AD 在 `recoverFirstAcceptedRetry → eventually → driver769:16` 因未观察到真实 running 停止；AE 在 `retryOriginalKnownRejections → eventually → driver919:20` 因唯一 retry 再次 failed 停止。复现仍为原 Table 两镜生成图片、恢复后提交原两视频、原红唯一 retry；原次数、冷却、running、时长与 unknown 不重投门禁保持。

与此前无 ACK 的缺口区别在于，本轮保存观测只等待已有原提交并读取原 owner API，没有额外保存或生成：AD **22 commit / 22 HTTP200 ACK / revision26**，AE **24 commit / 24 HTTP200 ACK / revision28**。两轮原 ACK 后的 task GET 与 my-document GET 均证实同一红 retry 为 failed/retryCount1、媒体为空，最终失败图持久化到达。AD 的[F只读复核](../../.runtime/agnes-storyboard-media-ad-v8-f-failure-observation-peer-v1/receipt.json)实际 OS0，170项核对和31个原件 guard通过；该结论不补旧轮 ACK、不把失败保存成功改判为供应商成功。原始依据为[AD运行收据](../../.runtime/agnes-storyboard-media-ad-v8-preparation-v1/receipt.json)、[AD浏览器原件](../../.runtime/agnes-storyboard-media-ad-v8-preparation-v1/live-temp/test_original_table_geometry_m0/browser/report.json)、[AE运行收据](../../.runtime/agnes-storyboard-media-ae-v8-preparation-v1/receipt.json)与[AE浏览器原件](../../.runtime/agnes-storyboard-media-ae-v8-preparation-v1/live-temp/test_original_table_geometry_m0/browser/report.json)。

Root 已实看两轮各两张原 PNG 与 fresh/failure 界面截图，仅白桌单红立方体、单蓝圆柱小场 `passed_limited`；[AD内容收据](../../.runtime/agnes-storyboard-media-ad-v8-root-content-review-v1/content-verdict.json)和[AE新内容收据](../../.runtime/agnes-storyboard-media-ae-v8-root-content-review-v1/content-verdict.json)为依据，AE 新 SHA 为 `08436f7395133b63c1859126ec1c6f9d7dbda92227e731bbf9228c35056562a8`。无视频可验；截图不单独证明保存 ACK。两轮未走到成功后的完整 Python 媒体/权限/HTTP/duplicate-stale AMQP 后置，worker 共享报告 errno13 重读修复没有在本轮成功后置中验过，完整验收仍 false。

两轮原 fixture 的三桶创建与原 remove_bucket 调用均完整返回，额外删桶0；Root 动态捕获每轮36个进程身份和精确 MySQL fullID，独立 collector 实际 OS0。[AD资源收据](../../.runtime/agnes-storyboard-media-ad-v8-root-final-resource-review-v1/receipt.json)与[AE资源收据](../../.runtime/agnes-storyboard-media-ae-v8-root-final-resource-review-v1/receipt.json)仅 `passed_recorded_live_snapshot_scope`：记录身份、精确容器、端口、UUID AMQP 与三桶无残留，原15服务、49产品 guard、两源及另采两组件保持。不证明未来/脱离 pytest 子树的进程，不以清理代替业务验收。

<a id="r19-single-png-current"></a>

## 2026-10-12 R19 增量：单 PNG immediate 场实际通过

Root 执行冻结的 **1440 / image / lost-upload-receipt / immediate** 单场，原 pytest 实际 **1 passed / 0 skipped / 1 warning / 38.40s**，wrapper OS0，[运行收据](../../.runtime/r19-pending-image-immediate-live-root-v1/receipt.json)与[浏览器原件](../../.runtime/r19-pending-image-immediate-live-root-v1/temp/test_original_browser_recovers0/browser/report.json)齐。160×90四象限 PNG 经 getImageBlob、实际 img 解码和像素核对，丢失上传回执后仍保持原 File 字节/名称；显式保存复用原 key、声明和 ready identity，cookies-only reload 零重传，稳定节点及资源唯一性、owner/member/outside、HEAD/Range、MinIO 原字节等原断言到达，无图片 durationMs/数据库 duration，模型提交0。

原三桶 remove 完整，额外删桶0；20个产品 guard 保持。原15服务与两源的独立 before/after 身份采样均实际 OS0。wrapper 原收据仍保留 `overall_resource_cleanup_passed=false`，它未捕获 MySQL fullID；Root 随后独立 capture **19个记录进程身份**和精确 fullID，collector 实际 OS0，[最终资源收据](../../.runtime/r19-pending-image-root-identity-v1/final-resource-review.json)仅证明该记录 snapshot、精确容器与端口无残留，不覆盖未来或脱离子树的进程。Root 实看原 pending/fresh 截图仅认证该单 PNG 场。原16音视频合同保持，未据此声称全部 R19、真实供应商或完整迁移通过。

<a id="r21-pending-source409-current"></a>

## 2026-10-12 R21 增量：真实 409 与草稿保持到达，测试仍失败

旧 run-a 在 setup 遇到 MySQL **OperationalError 1044 / Access denied … to database …_test**，JUnit 1test/0failure/1error/0skip，没有浏览器报告，[旧运行收据](../../.runtime/r21-unsaved-source409-preparation-v1/evidence/run-a/receipt.json)保留。Root 后用独占临时 MySQL launcher 实际运行，结果 **OS1 / 1 failed / 0 skipped / 1 warning / 81.65s**，不修改原 runner，[隔离运行收据](../../.runtime/r21-unsaved-source409-root-isolated-launcher-v1/evidence/run-a/receipt.json)保留。

[本轮浏览器原件](../../.runtime/r21-unsaved-source409-root-isolated-launcher-v1/evidence/run-a/browser/report.json)已观察 sidebar 与 preview 两处真实 `canvas_source_content_conflict` HTTP409、就近 alert；before-source-change、after-sidebar-409、after-preview-409 三次本地草稿字面内容均保持，保存仍 pending、同提交 matching request1/ACK0/forward0，远端原文与 revision2保持。随后测试 helper 自己发送 Escape，使 preview 消失，在刷新目录步骤 `verify-r21-source409.mjs:493:86` 的 `locator.click` 发生 **Timeout 30000ms exceeded**。这些局部事实不等于完整 R21 通过，显式刷新/重选后的完整路径仍待实际重验；模型提交/对象上传0。

P 派生[新v2准备收据](../../.runtime/r21-unsaved-source409-preparation-v2-p/preparation-receipt.json)：去除该 Escape，并在 click 前断言可见，原 test/helper 字节保持；静态 OS0、10 candidate与44 source guard，manifest SHA `a7c604c5ce853b2d6abf9d4c6cd6e74e044012dc99fd3bc982baafb1b2a6b1ce`。Root 随后实际执行[独占v2新轮](../../.runtime/r21-unsaved-source409-preparation-v2-p/evidence/run-a/receipt.json)，仍 **OS1 / 1 failed / 0 skipped / 1 warning / 66.59s**。[v2浏览器原件](../../.runtime/r21-unsaved-source409-preparation-v2-p/evidence/run-a/browser/report.json)到达两处真实409和两次pending草稿观察、ACK0/forward0；after-preview-409观察器在 `assertPendingDraft → editBox → eventually` 的固定双击点被预览遮挡，报“原生文本编辑器双击落点被遮挡”，第三次draft观察未完成，不沿用旧轮三次事实。P继续诊断，未预写下一轮通过。原15与两源前后保持，Root另采19个记录身份及精确fullID，[v4资源收据](../../.runtime/r21-source-unsaved-root-identity-v4/final-resource-review.json)仅该snapshot清理通过；两个旧失败与v2新失败原件全部保留。

P 后续仅准备 v3：在正文固定 y55、三个候选 x 位置中检查真实 DOM 命中同一节点，再普通双击并记录 bounds/候选/落点；当前尚未 live，不把派生准备写成已修复验收。产品、门槛及全部旧证据保持。

<a id="r22-panel-profile-current"></a>

## 2026-10-12 R22 增量：面板候选无 mutation 收益，当前 profile 只作诊断

panel-width v3 的[静态收据](../../.runtime/r22-panel-width-component-preparation-p-v3/static-check-receipt.json)不含真实组件执行；Root 随后的[真实组件运行原件](../../.runtime/r22-panel-width-component-preparation-p-v3/executions/run-ecd9c6f9-4e1d-4dda-9d1e-7c15c20a5237/report.json)才证明 old/candidate 各30 case行为、回调、几何与实际订阅保持。相同 width/transform 的40次 preview 两侧 mutation均0；同 width而 transform变化两侧均40；其它比较也没有观察到 mutation减少，不能从零记录推断 setter 没有调用。候选未应用、产品 mutation0，未证明性能改善或正式32ms通过。

当前 target1000单轮 profile 的[结构运行收据](../../.runtime/r22-textdata-target-diagnostic-preparation-p-v1/evidence/run-textdata-target-profile-root-a/receipt.json)记录实际 **before0 / trace0 / parser0 / after0**、`diagnostic_complete=true`与`budget_result=null`，不由解析摘要反推退出码。该轮[profile报告](../../.runtime/r22-textdata-target-diagnostic-preparation-p-v1/evidence/run-textdata-target-profile-root-a/browser/report.json)为 `diagnostic_profile`、`profile_enabled=true`、`frame_budget_1000_passed=null`；[离线解析摘要](../../.runtime/r22-textdata-target-diagnostic-preparation-p-v1/evidence/run-textdata-target-profile-root-a/browser/diagnostic-summary.json)仅供事件分析，预算结论仍 null。既有 Text.data/v6 正式32ms失败、range0中心、全页视觉与GPU呈现等未验范围保持，诊断没有关闭正式性能验收。

<a id="modelhub-paid-budget-current"></a>

## 2026-10-12 授权增量：仅备用 ModelHub 视频，尚未执行付费验收轮次

新用户授权只扩展到 **ModelHub `minimax-h3` / `480p`**，全任务最多 **5个 generation POST**、累计声明视频时长 **≤30秒**，初始两场各5秒；公开合同 duration为整数5..15。旧“收费暂缓”记录作为当时边界保留，本次新授权改变的是这一项备用视频范围，独立真实音频、企业授权及其它收费能力仍暂缓。P 两文件候选已由 Root 实际应用，[应用收据](../../.runtime/modelhub-minimax-h3-application-root-v1/application.json)记录适配器及新增测试、其余8项 guard保持，无前端/ORM/DDL/依赖变更。Root 已实际观察新增60项单测全部通过；完整 unit/API 首轮自然 **OS1 / 2849 passed / 90 errors / 2 warnings / 560.21s**，可见setup关键栈为 `_pytest.tmpdir → pathlib.find_prefixed → os.scandir` 对系统 `Temp/pytest-of-snow` 抛 **WinError5**。Root已用新独占工作区 basetemp/JUnit 执行完整复验，截至本节未终态，不将首轮或复验预写为通过；60项已过单测不重复执行。

F 的[预算冻结 manifest](../../.runtime/modelhub-budget-20261012-f-v1/manifest.json)和[离线验证收据](../../.runtime/modelhub-budget-20261012-f-v1/offline-validation-v2.json)记录37 passed/0 skipped、Ruff/格式/AST/freeze实际 OS0；这仅是临时 SQLite 与发送回调合同。全部后续轮与进程复用 Root 唯一绝对总账，5次/30秒以 BEGIN IMMEDIATE 原子预留；异常、unknown或进程中断不退款，同任务键即使body改变也拒绝重发，已有reservation不能第二次派发。模块不读取密钥、不发 HTTP，F 未初始化或查询真实总账。Root随后已真实首次初始化总账，实际OS0、0次/0秒、上限5/30、无refund/reset，并复核预算freeze OS0；初始化没有供应商POST。**本节截止新付费轮次未run、供应商 generation POST为0**；AF及后续结果另按实际原件追加，不以预算合同或单测代替真实供应商验收。

全部旧失败和34个历史 unknown保持、不重投；完整 R10/M0–M5、一比一迁移、同版部署/可信公网首帧、R23未批准行为仍开放。本次维护整理仅准备四文档追加候选，未直接应用正式文档、修改产品或运行新模型/浏览器/服务/资源查询。


<a id="r21-v3-limited-current"></a>

## 2026-10-12 R21 接续：v3 单1440来源409与未ACK草稿场通过

Root 执行 v3-p-v2 新独占轮，原 pytest 实际 **1 passed / 0 skipped / 1 warning / 37.66s**、runner OS0，[运行收据](../../.runtime/r21-unsaved-source409-preparation-v3-p-v2/evidence/run-a/receipt.json)及[浏览器原件](../../.runtime/r21-unsaved-source409-preparation-v3-p-v2/evidence/run-a/browser/report.json)为依据。观察器只在正文 y55 的三个候选 x 点中核对实际 DOM 命中同一节点，记录 bounds/候选/落点后普通双击；没有产品修改或降低原断言。此前 setup1044、v1 Escape/clickTimeout、v2 固定落点被预览遮挡的实际失败全部保留。

本轮 report为complete，9个checks全部true：两处真实 `canvas_source_content_conflict` HTTP409与就近alert，4次pending草稿观察保持字面内容，明确刷新目录/重选不覆盖本地输入；同一被控制延迟的commit只转发1次，真实 ACK200，最终revision3，owner my-document与原生输入一致，生成提交/资源写入/legacy source read均0。控制点在正式后端转发前，不声称供应商或服务端已接受后丢ACK。Root 实看 pending-source409.png 与 draft-real-ack.png，只认证该1440单来源场，不扩大到完整来源矩阵或媒体供应商验收。

原15服务与两源独立before/after采样均实际OS0；Root捕获v5 **19个记录进程身份**与精确MySQL fullID，collector实际OS0，[v5资源收据](../../.runtime/r21-source-unsaved-root-identity-v5/final-resource-review.json)仅记录snapshot、精确容器与端口无残留，未来/脱离子树的进程仍不覆盖。既有未通过轮次不由这个新单场改判。

<a id="modelhub-unit-af-early-current"></a>

## 2026-10-12 ModelHub 接续：完整后端复验通过，AF在fixture解密前置失败

Root用新独占工作区basetemp/JUnit完成完整unit/API复验，实际 **2939 passed / 0 skipped / 2 warnings / 635.39s / pytest OS0**；适配器及新增测试两文件 scoped format实际OS0，[结构收据](../../.runtime/modelhub-minimax-h3-verification-root-v2/receipt.json)和[JUnit原件](../../.runtime/modelhub-minimax-h3-verification-root-v2/junit.xml)齐。首轮系统Temp/pytest-of-snow路径WinError5导致2849passed/90setup errors/OS1的历史保持，不以复验覆盖原退出码。本次unit/API不依赖业务数据库或真实供应商，不能作为集成或付费模型验收。

AF/v8-v1虽已真实启动，仍提前 **1 failed / 0 skipped / 1 warning / 6.95s / OS1**；[AF运行收据](../../.runtime/modelhub-storyboard-media-af-v8-preparation-v1/receipt.json)及[原日志](../../.runtime/modelhub-storyboard-media-af-v8-preparation-v1/live.log)保留。关键栈为 `core.crypto → cryptography.exceptions.InvalidTag → ValueError: Invalid encrypted key envelope`，在fixture加载ModelHub持久凭据时失败，8个角色与浏览器尚未启动。U已核对根因为fixture的32字节测试master误解主Settings保存的envelope；Agnes live_key原路径使用Settings cipher，AG仅派生修正持久loader使用相同Settings cipher，fixture模型检查用key与产品/主密钥保持，尚未据此声称AG运行通过。

Root确认本轮供应商POST0、唯一共享总账仍0次/0秒，max5/30、无退款/reset；这次前置失败没有占用或重置预算。原fixture三桶remove完整，以AF原收据为准；runner日志只证明唯一MySQL名称的cleanup返回。Root另作[有限after复核](../../.runtime/modelhub-af-root-supplemental-after-v1.json)，实际OS0，原15/49guard/两源及另采两组件SHA保持，日志唯一MySQL名/端口/cleanup匹配、Docker按精确名称查无该容器。**AF没有live snapshot或MySQL fullID捕获**，因此不称完整进程/fullID资源清理或整轮通过。

此前自动审批曾拒绝原动作；Root核对合成红立方体/蓝圆柱、direct text-only、新随机fixture独占数据库证据后，同一原动作重新获得批准，未绕过审批，当前没有待用户追加确认。后续只按已授权的 ModelHub minimax-h3/480p、全任务≤5个generation POST/累计声明≤30秒和唯一持久总账执行，初始5+5秒；付费媒体本轮仍未开始验收。AG与后续结果另按自然运行原件追加，不把loader修正或unit/API通过当真实供应商通过。

AD/AE、R19及所有旧失败/34历史unknown保持；panel候选未应用/正式32ms仍未过、range0中心/全页、完整R10/M0–M5/一比一、同版部署/可信公网首帧及R23未批准范围继续开放。独立真实音频/企业及其它收费能力仍未扩授权。本增量只准备四文档append候选，正式文档尚未应用，F未新增模型、浏览器、服务或资源查询。


<a id="modelhub-ag-api422-current"></a>

## 2026-10-12 ModelHub AG：两张图片内容有限通过，视频在 API 参数校验阶段停止

AG/v8-v1 修正 fixture 持久凭据的解密来源后，由 Root 在新独占轮实际运行并自然结束为 **OS1 / 1 warning / 273.18s / not_passed**；[运行收据](../../.runtime/modelhub-storyboard-media-ag-v8-preparation-v1/receipt.json)、[JUnit](../../.runtime/modelhub-storyboard-media-ag-v8-preparation-v1/junit.xml)和[原日志](../../.runtime/modelhub-storyboard-media-ag-v8-preparation-v1/live.log)保留。JUnit 为 tests0/failures0/errors0/skipped0，suite time272.593s，不能写成1failed或通过。浏览器停在“video原Table批准两镜并自然后台执行”，`AssertionError: 真实分镜媒体状态未达到预期` 的调用栈为 `eventually:80 → runStage:1099 → driver:1304`；完整原报错和原图状态见[浏览器报告](../../.runtime/modelhub-storyboard-media-ag-v8-preparation-v1/live-temp/test_original_table_geometry_m0/browser/report.json)。旧 AF 解密失败及所有更早失败保持原结果。

两次 Agnes 图片 generation POST 实际 HTTP200，各自任务 succeeded/READY、原消费者 attached 并回填。Root 用 original 分辨率实际审看归档的两张真实 PNG，且 PIL 完整 decode：镜1纯红立方体、镜3纯蓝圆柱，均1024×1024，白桌柔光、单物体、无可见文字/logo/人物/额外物体。红图791812 bytes、SHA `df6f8657e1dfd01846603ad0b6a392118a061424aaeb1d471cd21008c4684207`；蓝图777574 bytes、SHA `8e3f7f1f036ba5db04357e2a2e5ed3e041df79f20ad2bb75276f5fa9b919fb57`。[Root 内容收据](../../.runtime/modelhub-ag-root-image-content-review-v1.json) SHA `debaaeba0f0533d4c575efba1d20c67af3dd1c77e057e617fea586da5a18bfe2` 为 `passed_two_image_content_checks`，videos_reviewed0、full_run_passed=false；只认证两图的有限内容，未由 F 重看，不作为图片整场或完整分镜媒体验收。

原 UI 两项视频提交都收到业务 API HTTP422：`canvas_generation_option_unsupported`，信息为“当前生成协议尚未支持画布参数 videoArkPrivateAssetUpload，未发送模型请求”。两份实际 body 均为 `videoSeconds="5"`、`videoArkPrivateAssetUpload="true"`、`vquality="720"`；720不是获授权的480p。请求没有发至 ModelHub，无真实视频产物、播放或内容检查，不能称供应商失败或真实5+5秒视频验收。未执行自动重投；参数来源和授权480p约束须先具体核对，再准备新独占轮，不能绕过 admission 或放宽预算。[Root 唯一总账只读终态收据](../../.runtime/modelhub-ag-root-budget-after-v1.json) SHA `c07acff0af637be60a2e76f05e054fdda50c95a31b226f6cf4252f17a3132e81`：inspect OS0、reserved0次/0声明秒、上限5POST/30秒、无退款/reset；F只读该收据，未查询或修改真实总账。

Root 已捕获本轮存活子树身份与 MySQL fullID，随后[资源终态复核](../../.runtime/modelhub-storyboard-media-ag-v8-root-final-resource-review-v1/receipt.json) SHA `fc710c2be961c52fffd5abc5d9922cce52d1d1e6440c7e7fc75c9c95efe9a478` 为 `passed_recorded_live_snapshot_scope`：36个记录进程当前不在、3个精确端口无监听、原 fullID 容器按精确不存在错误及名称复核、当前 broker 身份/namespace核对、3个原桶不存在；原15服务、49产品 guard与两源 before/after 保持。[另两实际产品文件 after 收据](../../.runtime/modelhub-ag-root-supplemental-after-v1.json) SHA `4961ad74c1b803e84c5d28c3063d1aa93aea5d3070261c315d1cd38858ce4a33` 也保持。只证明 captured snapshot 和本轮记录身份，不覆盖之后新建或脱离 pytest 子树的进程，不称完整资源清理；清理通过不改变 AG 验收 OS1。

本增量只准备四份维护文档的追加候选；F只读本地原件、核对链接/摘要和前缀，不新增供应商、浏览器、服务、业务API、资源或真实总账查询。正式文档尚未应用。旧34历史unknown不重投，完整R10/M0–M5/一比一、正式32ms、同版部署/可信公网首帧及R23等独立门禁保持；不把 AG 的参数失败扩大为新的无限验收矩阵。


Root ?? 2026-10-12T05:33:11.080766+00:00 ??????????????????????????????????????????????AG ? OS1 ?????????? [Root ????](../../.runtime/modelhub-ag-maintenance-application-root-v1/application.json)?


<a id="modelhub-ah-auth-current"></a>

## 2026-10-12 当前增量：视频参数修复已验证，AH 真实供应商返回 401

AG 参数来源问题已按实际产品调用链修复，仅修改 `frontend/canvas/src/lib/model-selection.ts`、`src/services/api/generation-task.ts`、对应 Node 测试及 `source-adaptations.json`。只有视频模式在宿主逻辑 ID 未声明协议或能力契约时保留工作区默认值；显式契约和节点参数优先级保持，图片仍为原默认比例。普通视频按模型能力过滤声音、水印，Ark 开关只发给已声明的 Ark 视频协议，支持的 false 保留，其他模式和 RunningHub 保持。未改后端 admission 或本轮 runtime profile。[实际应用收据](../../.runtime/modelhub-canvas-video-defaults-application-root-v2/application.json)保留原应用时点的 verification_pending；随后独立[Root 终态验证](../../.runtime/modelhub-canvas-video-defaults-verification-root-v2/receipt.json)才证明应用后 `npm test` 为 **524 passed / 0 failed / 0 skipped**、`npm run source:check` 与宿主 `npm run build` 均自然 OS0。统一构建含两包类型检查和构建，Vite 导入及大 chunk 提示保留原日志；[独立只读复核](../../.runtime/modelhub-canvas-video-defaults-readback-u-v2/receipt.json)核对六日志、四产品及历史收据。上述离线结果不是供应商验收。

Root fresh 采集并复核原 15 服务、两固定源和当前 54 产品身份，冻结 AH 新基线后只运行一次。AH 原始两视频请求的只读前置门禁全部通过，均为 minimax-h3、5 秒、480p、16:9、direct text、无参考媒体和三个不支持开关；合法请求原样发送。[AH 运行原件](../../.runtime/modelhub-storyboard-media-ah-v8-preparation-v1/receipt.json)实际 **OS1 / 1 warning / 98.29s / not_passed**；[JUnit](../../.runtime/modelhub-storyboard-media-ah-v8-preparation-v1/junit.xml)为 tests0/failures0/errors0/skipped0、suite97.712s，不能记作1failed或通过。

两项业务生成 API 已接收为任务，正式视频 Worker 各向 `api.modelhub.cc/v1/videos/generations` 发出一次 POST，参数均为 minimax-h3、duration5、resolution480p、ratio16:9、functionMode=omni_reference；两次供应商均 **HTTP401**，无供应商 task ID、无视频产物，业务任务明确 failed/provider_auth。浏览器停止于 `AssertionError: 原任务不属于可核验的活动、完成或明确未受理状态`，关键调用栈 `classifyTasks:22 → observeOriginalState:990 → runStage:1185 → driver:1330`。复现步骤为原 Table 编辑红立方体/蓝圆柱两镜、生成 Agnes 图片、恢复后从原 Table 提交两视频；没有重投或用测试替身替换响应。[浏览器原件](../../.runtime/modelhub-storyboard-media-ah-v8-preparation-v1/live-temp/test_original_table_geometry_m0/browser/report.json)与[失败自然对账](../../.runtime/modelhub-storyboard-media-ah-v8-preparation-v1/live-temp/test_original_table_geometry_m0/reconciliation.json)保留，原四任务 API/DB/record 终态一致，active/unknown/read_errors 均空，未到完整成功后 Python 媒体、权限、HTTP 和 AMQP 后置。

两张 Agnes 图片仍实际 POST200、succeeded/READY、attached 回填；Root 已 original 分辨率实看并 PIL 完整 decode，两图1024²，纯红立方体755099 bytes、纯蓝圆柱815088 bytes，白桌柔光、单物体、无可见文字/logo/人物/额外物体。[Root AH 自然终态及两图内容收据](../../.runtime/modelhub-ah-root-terminal-review-v1.json)仅 `passed_two_image_content_checks`，videos_generated/reviewed0、full_run_passed=false，不写图片整场或分镜媒体通过。同收据只读唯一总账为 **2次/10声明秒**，剩余3次/20秒；两次401不退额度。全任务仍同一 `backend/.runtime/modelhub-minimax-h3-budget-20261012.sqlite3`，最多5生成POST/30声明秒，禁止复制、初始化、退款、reset及历史unknown重投。

本轮八角色、Node、Chrome实际身份捕获为[limited capture](../../.runtime/modelhub-storyboard-media-ah-v8-root-runtime-identity-v1/observation.json)，34条记录；自然 finally 后[collector](../../.runtime/modelhub-storyboard-media-ah-v8-root-final-resource-review-v1/receipt.json)实际OS0、`passed_limited_recorded_pid_name_scope`，精确MySQL fullID/名称、原AMQP namespace及三个测试桶不存在，原15服务/54产品/两源保持。捕获有变化，不能将OS0写成完整 live-tree 清理；仅记录PID和精确名称范围，不覆盖未来、脱离子树或外层wrapper。清理不改变AH验收OS1。

用户随后提供新凭据并授权重试；Root已仅原子更新原服务端 encrypted envelope，[加密更新收据](../../.runtime/modelhub-provider-key-update-root-v2.json)实际OS0、roundtrip通过、主密钥未改、账本保持2/10，未写明文。后续使用独立AI新轮；两原视频正常路径预计再发5+5秒，累计4次20秒，明确未受理429的原65秒许可重试仍受全局5次30秒硬门禁，unknown不重发。新轮准备及结果另补，不预写通过。

R22 视口候选仍未应用。Root此前独占真实DOM old14/candidate14均通过、parity与前后guards为true；[原DOM报告](../../.runtime/r22-live-viewport-dom-preparation-p-v1/executions/run-6c6b5560-7f2a-4b85-88a3-2fa8d02b1d70/report.json)及[只读复核](../../.runtime/r22-live-viewport-dom-review-p-v1.json)证实同值setter减少、有限容器mutation减少，面板mutation未减少。该结果未测StyleInvalidator、GPU或正式32ms，节点drag只是utility预览，完整拖拽保存、中心/全页/触屏与完整泄漏仍未关闭。生产对照的新[准备说明](../../.runtime/r22-live-viewport-production-matrix-preparation-p-v1/README.md)绑定本次U应用和真实验证，随后独占构建/矩阵结果另记。

本追加由Root实际应用后生效，supersede旧段的“正式文档尚未应用”准备状态；旧AG/AF及所有历史失败字节保留。34历史unknown、完整R10/M0–M5/一比一、正式32ms、同版部署/可信公网首帧、R23未批准范围及独立真实音频/企业授权仍开放，不新增无限验收排列。


<a id="modelhub-ai-read409-current"></a>

## 2026-10-12 AI 自然终态：详情 GET 409，尚未验证新视频凭据

新凭据已安全加密更新后，Root fresh核对54产品/原15服务/两源并冻结AI，单次真实运行自然 **OS1 / 1 warning / 78.00s / not_passed**；[运行原件](../../.runtime/modelhub-storyboard-media-ai-v8-preparation-v1/receipt.json)和[JUnit](../../.runtime/modelhub-storyboard-media-ai-v8-preparation-v1/junit.xml)保留，JUnit tests0/failures0/errors0/skipped0、suite77.394s，不记作1failed或通过。

本轮两Agnes图片各真实POST200，失败后同任务读回均succeeded/READY。在图片阶段等待任务/回填时，验收观察器详情GET收到 **HTTP409 / canvas_binding_changed**：“批量读取期间画布或可信来源已变化，请重新读取”，原read立即断言中止；关键栈 `read:72 → Promise.all(index0) → eventually:80 → runStage:1255 → driver:1329`。复现为原Table两镜真实图片生成、running恢复后并行读取原两详情；[浏览器完整失败](../../.runtime/modelhub-storyboard-media-ai-v8-preparation-v1/live-temp/test_original_table_geometry_m0/browser/report.json)与[自然失败对账](../../.runtime/modelhub-storyboard-media-ai-v8-preparation-v1/live-temp/test_original_table_geometry_m0/reconciliation.json)保留。该code也可能表达可信来源绑定变化，不能只凭409判定autosave或吞掉来源/权限冲突。

视频阶段未到、ModelHub生成POST0、无视频，**新key尚未实际验证供应商鉴权**。Root唯一账本只读仍2次10秒、剩余3次20秒，未退旧401额度。两张PNG已original实看/PIL完整decode：红立方体621420 bytes、蓝圆柱766421 bytes、均1024²，白桌单物体、无可见文字/logo/人物或额外物体；[Root限定终态/内容收据](../../.runtime/modelhub-ai-root-terminal-review-v1.json)只认证两图有限内容，不认证完整回填/图片整场或整轮。对账无active/unknown/read_errors，原任务/record终态一致，未到完整成功后Python后置。

Root capture实际晚于自然终态，未捕获存活进程，processes0；[原capture](../../.runtime/modelhub-storyboard-media-ai-v8-root-runtime-identity-v1/observation.json)为captured_limited，不能声称完整存活树。随后[collector](../../.runtime/modelhub-storyboard-media-ai-v8-root-final-resource-review-v1/receipt.json)实际OS0、passed_limited_recorded_pid_name_scope，限定原记录PID/精确名称、桶/AMQP、原15/54/两源保持，不补造初始fullID或完整清理。旧AI失败原件不改判。

AJ只准备窄观察器修复：仅原十进制任务详情同URL GET，在精确409/code/message下最多3次、间隔150ms重读；最终响应仍交原read和原任务/节点/来源/权限断言，其他HTTP/非法JSON/网络错误不重试，第三次仍409继续失败。不改列表、my-document、POST、产品、原234/326断言、原deadline或unknown不重投规则。新轮准备、fresh基线及真实结果另补。额外双域名随机任务GET鉴权诊断曾被自动审批以“未明确授权两个凭据目的地”拒绝；整个exec未执行、脚本/收据均未创建、GET0。该额外诊断取消，后续仅原已核准api.modelhub.cc正式入口，不由加密保存推断新key可用。

R22生产候选两套隔离产物的[实际构建收据](../../.runtime/r22-live-viewport-production-matrix-preparation-p-v1/builds/build-root-owned-a/receipt.json)为before/candidate各OS0，原产品viewport未改；但[首次before运行](../../.runtime/r22-live-viewport-production-matrix-preparation-p-v1/evidence/run-root-before-a/receipt.json)为before guard0/benchmark1/after guard0，[浏览器报告](../../.runtime/r22-live-viewport-production-matrix-preparation-p-v1/evidence/run-root-before-a/browser/report.json)cases为空，`TypeError: Cannot read properties of undefined (reading 'launch')`，driver314。原因是准备脚本把require条件解析的Playwright CJS路径作ESM动态import后读取named chromium；浏览器未launch，没有54组帧数据，不是产品性能结论。P仅派生新v2修正模块加载，保留v1原件和正式32ms/54组门槛；新v2实际运行另补。

本次Root正式追加保留前文全部字节；AI读409、R22脚本失败及旧AH401分别保留，不能合并改为成功。34历史unknown、全部独立门禁和完整迁移goal仍active。


<a id="modelhub-aj-ak-save-wait-current"></a>

## 2026-10-12 AJ/AK 与 R22 最新实际结果：新视频凭据仍未验到

AJ 在首次画布加载时自然 **OS1 / 1 warning / 110.42s / not_passed**；JUnit tests/failures/errors/skipped 均为0，suite109.315s，不能记作1failed或通过。浏览器停在 `step=start`，`locator.waitFor: Timeout 30000ms exceeded`，关键栈 `open:442:50 → driver:1331:5`。Root实看原失败图只见加载圆圈，不足以判定构建并行因果或产品缺陷。[AJ Root终态原件](../../.runtime/modelhub-aj-root-terminal-review-v1.json) SHA `1d314abb75f9e012da85fa7178eafc9331829fc814d094717abedc9cc6f881bc` 与[原运行收据](../../.runtime/modelhub-storyboard-media-aj-v8-preparation-v1/receipt.json)保留。提交、stages与全部task IDs为空，Agnes图片和ModelHub视频 generation POST均0，未验证待验视频凭据。

AJ原[collector收据](../../.runtime/modelhub-storyboard-media-aj-v8-root-final-resource-review-v1/receipt.json)仍为 **OS1 / invalid_original_task_ids**，在空ID检查时停止，原件当时没有完成资源查询。Root随后使用显式零任务入口实际执行[新独占回查](../../.runtime/modelhub-aj-zero-task-resource-review-root-v1/receipt.json)，自然 **OS0 / passed_limited_recorded_pid_name_scope**，SHA `a1be2e40a3673864bc2c2940c2c3b962bd869a21302814701fd0322636fdcc48`。只允许原 `step=start`、submissions/stages/task IDs空且自然失败的情形，任务对账为 **not_applicable**，空集合的DB/API布尔值不证明任何任务终态，供应商验收仍失败。未attempt live capture，未补造PID、StartTime或完整历史容器ID；新查询实际确认8个原记录PID不存在、4253/53135/60640无监听、原随机MySQL精确名称不存在、原UUID AMQP队列/exchanges与三原随机桶不存在，原15服务/54产品guard/两源及原件前后保持。仅证明实际记录PID、name/port和精确资源名，不称完整历史live-tree/fullID清理；旧collector1不改判。

AK由Root在新独占轮实际运行，自然 **OS1 / 1 warning / 221.51s / not_passed**；JUnit tests/failures/errors/skipped均0、suite220.504s，不能记1failed或整轮通过。停在图片阶段原任务活动状态与cookies恢复安排，`locator.waitFor: Timeout 30000ms exceeded`，关键栈 `persistedPreClose:516:73 → runStage:1172:23 → driver:1334:5`。复现为原Table两镜生成Agnes图片，进入关闭恢复前的持久化检查，再等待顶栏保存状态可见；[AK浏览器原件](../../.runtime/modelhub-storyboard-media-ak-v8-preparation-v1/live-temp/test_original_table_geometry_m0/browser/report.json)、[自然对账](../../.runtime/modelhub-storyboard-media-ak-v8-preparation-v1/live-temp/test_original_table_geometry_m0/reconciliation.json)与[Root终态内容读回](../../.runtime/modelhub-ak-root-terminal-review-v1.json) SHA `424879083630da89c308d667acb479c39d721df5fd10534560abef1e42e038dc`为依据。原两任务及record均succeeded，active/unknown/read_errors空；这不补足完整媒体成功后置。

本轮两Agnes图片generation POST均HTTP200，原画布已显示回填图片。Root已按original尺寸实际审看且PIL完整decode：两图均PNG/1024×1024，白桌单红立方体、单蓝圆柱，无可见文字/logo/人物或额外物体。红图798016 bytes、SHA `b57e229e8aa2f476d554078fb1c1cda2eb303d434dadd472feeb80a71f8c50e1`；蓝图786014 bytes、SHA `f128000d37c79c3f70cfcae4710ba46d2accd910f3a7277a5412eb85450d9f4b`。仅这两图有限内容通过，full_run_passed=false，不能写图片整场或分镜媒体通过。原18次commit均收到成功ACK，最高revision22，最后ACK距超时369ms且最终已commit图包含媒体内容；Root实看失败截图顶栏仍为等待图标。这不足以断言“数据未保存”或产品错误，也不能吞掉原30秒可见保存状态超时断言。

AK未到视频阶段，ModelHub generation POST0、无视频产物/播放/内容验收，待验视频凭据没有真实供应商鉴权证明。Root本次通过AK `modelhub_budget.py inspect` 实际只读唯一绝对总账并自然OS0：posts_reserved2、seconds_reserved10、max5/30，仍只有AH两项response预留，无新增项，剩余3次20声明秒；不由POST0推算总账，不退旧401额度，不复制/初始化/reset账本，不重投历史unknown。本次文档准备未自行查询真实账本。

Root曾实际捕获AK的36个记录进程与MySQL fullID，随后[AK资源collector](../../.runtime/modelhub-storyboard-media-ak-v8-root-final-resource-review-v1/receipt.json)自然 **OS0 / passed_recorded_live_snapshot_scope**，SHA `b7700de635063d50700d1da880904684863a05b814c0831e735c9753c5e53a27`。原15服务/54产品guard/两源保持，原snapshot进程、精确容器及3端口、原AMQP和三桶范围查询通过。只覆盖已捕获且原件记录的身份，不覆盖未来新建或脱离pytest子树的进程；资源复核0不改变AK验收OS1。

AL不是已冻结或已运行的新验收轮：Root实际预冻结自然 **OS1**，`freeze_baseline.py:75` 读取 `preparation['preparation_uuid']` 抛 **KeyError: 'preparation_uuid'**，静态准备遗漏此字段。[AL冻结失败原件](../../.runtime/modelhub-al-root-freeze-failure-v1.json) SHA `c99903ef0c75c7cda0385b75e0ff27406ef71254163d8d329dce4d97b5cd6c29`保留。product-baseline不存在，Root已采fresh identity不能直接用于新轮；模型、浏览器和供应商尚未启动。U正在派生AM的窄metadata修正与纯离线消费key合同，未改产品，后续冻结和真实结果另记，不据准备声明待验凭据可用。

R22 v2两套owned生产构建此前各自然OS0；[构建收据](../../.runtime/r22-live-viewport-production-matrix-preparation-p-v2/builds/build-root-owned-v2-a/receipt.json)保留。Root随后实际before轮自然 **OS1，before/benchmark/after=0/1/0**，[运行收据](../../.runtime/r22-live-viewport-production-matrix-preparation-p-v2/evidence/run-root-before-v2-a/receipt.json) SHA `a37f3587ef508762f76360a6778d1330276c7105d2aa566e468cd6f24e1a7829`。source/target各100和1000的四case measured，每case9手势，仅36组；source5000第五case在初始图片预读稳定等待120000ms时失败，手势0，target5000未运行。[浏览器报告](../../.runtime/r22-live-viewport-production-matrix-preparation-p-v2/evidence/run-root-before-v2-a/browser/report.json) SHA `78844c531265613a35d3a70a84ea2ac433e1e265f13cfef0c66d51a2fb4ab826`记录2122个unique images，commits/viewport_writes/video_requests均0，page/http/fixture/unknown错误列表均空；关键栈 `waitAudit:152:58 → measure:211:9 → driver:316:69`。wrapper随后在 `run.py:150 → report_review:38` 因cases不足断言失败，report_review与预算flag均未生成，不能记作完整54组或正式frame_budget_failed。

已有target1000的局部p95如下，单位ms；局部zoom三项均超过32ms，但完整矩阵与完整预算结果仍未成立：

| round | drag p95 | zoom p95 |
| --- | --- | --- |
| 0 | 8.9 | 100.0 |
| 1 | 16.7 | 158.2 |
| 2 | 16.7 | 108.3 |

原初始化predicate要求commits/image_requests/document_bytes连续1000ms不变；source5000仍有原固定媒体预读活动，120秒上限先到。仅准备driver候选将这一处初始化waitAudit从120000改为360000；稳定条件、保存等待默认20000、原数据/媒体SHA、原生手势与保存/播放断言、54组矩阵和32ms门槛均保持。该候选没有执行prepare/freeze/build/run，不能保证360秒足够。R22 viewport产品候选未应用，candidate正式矩阵未启动，旧v1导入失败保持。

本增量由Root正式应用后生效，全部旧字节与失败原件保留。完整R10/M0–M5/一比一、完整媒体与正式32ms、同版部署/可信公网首帧、34历史unknown和R23未批准范围仍按原边界处理，不扩大两图有限通过或资源snapshot结论；独立真实音频/企业授权未由本轮新增。


Root 后续补记：AM 的静态 metadata 修正已实际完成，新合法 UUID 及消费者合同检查通过；原 AL 缺 UUID 的输入为红，修正输入为绿。AM 尚未采 fresh 基线、freeze 或 live，不计真实供应商调用。[AM 静态清单](../../.runtime/modelhub-storyboard-media-am-v8-preparation-v1/manifest.json)与[metadata 消费检查](../../.runtime/modelhub-storyboard-media-am-v8-preparation-v1/static-metadata-consumer-contract.log)为依据。为优先验证用户新视频凭据，主代理接着准备视频→图片的新独占轮；原 Table 的直接生成不依赖图片，两阶段及全部参数、任务、媒体、权限后置断言和同一 5POST/30秒预算保持，不增加额外鉴权生成请求。此补记不预写下一轮已准备、冻结或真实通过。


<a id="modelhub-an-r22v3-r19unavailable-current"></a>

## 2026-10-12 实际结果：新视频凭据有效，完整验收仍未通过

AN 单次真实执行确认新凭据有效：两条minimax-h3各5秒/480p/16:9 POST200，轮询至completed并下载741158、520833 bytes；两图Agnes POST200，四任务/记录/结果自然succeeded、API READY，active/unknown/read_errors为空。作者页和cookies-only重开后的两视频共四次真实播放推进超过0.1秒，两图解码、保存状态、稳定身份、重复不新增和28条权限请求已到。唯一总账实际累计 **4 POST / 20声明秒，剩1 POST / 10声明秒，上限5/30**；旧AH两次401保持，失败不退款、不重置。剩余一次保留，未再启动需要两个POST的整轮。

整轮自然 **OS1 / 1warning / 624.46s / JUnit四计数全0**，不记1failed或完整通过。成员播放失败stack：`verifyMedia:698:70 → driver:1376:61 / locator.click: Timeout 30000ms exceeded`。复现为原Table先生成视频再生成图片，成员重开共享图并适合屏幕后播放；截图显示图片覆盖视频中央按钮。固定源的image/direct-video同坐标且追加节点，新会话按数组层序绘制，因此不据阶段调序认定迁移布局bug。成员播放、完整Python DB/MinIO/媒体HTTP、duplicate-stale AMQP及最终acceptance后置未到。55commit/54个200 ACK/最高revision60，未ACK第17项保留。

Root两视频完整解码均OS0，ffprobe为 **1038×576、format5.167s、stream5.166667s**；两张1024²原图、每视频九点contact和第0/4/8原尺寸帧实看，仅单物/颜色/无文字人logo有限符合。红视频背景变化未证明固定镜头，纯色对称蓝圆柱转动未由采样证明，不扩为完整动态/逐帧/音频内容通过。另有算定不符：实际比例距16/9 **0.0243056**，超过冻结`check_stage:924`的 **<0.02**；该Python断言尚未执行，不冒充执行失败，不放宽阈值或转码。请求480p与实际576像素高度分别记录。collector自然0只证明37已捕身份/MySQL fullID/端口/UUID AMQP/三桶的`passed_recorded_live_snapshot_scope`，原15/54/两源保持，不覆盖未来或脱离子树进程。

原件：[AN终态v2](../../.runtime/modelhub-an-root-terminal-review-v2.json)、[浏览器报告](../../.runtime/modelhub-storyboard-media-an-v8-preparation-v1/live-temp/test_original_table_geometry_m0/browser/report.json)、[失败截图](../../.runtime/modelhub-storyboard-media-an-v8-preparation-v1/live-temp/test_original_table_geometry_m0/browser/failure.png)、[解码抽帧](../../.runtime/modelhub-an-root-content-review-v1/sampling-receipt.json)、[资源回查](../../.runtime/modelhub-storyboard-media-an-v8-root-final-resource-review-v1/receipt.json)。

R22v3 prepare/历史owned产物绑定均实际0，复用v2两套各874文件的成功构建，**本轮新Vite构建码null**。初始化等待仅120→360秒，原1秒稳定/保存/播放/非profile/32ms及每模式54组保持。before/candidate各完整54组，均自然 **OS1 / guard、benchmark、after=0/1/0 / frame_budget_failed / failure=null**；只读compare0、受控夹具行为/几何parity=true，不代表预算通过。

| 1000节点轮次 | before drag p95 ms | candidate drag p95 ms | before zoom p95 ms | candidate zoom p95 ms |
| --- | ---: | ---: | ---: | ---: |
| 1 | 16.1 | 9.0 | 91.8 | 91.7 |
| 2 | 16.7 | 16.7 | 116.7 | 166.8 |
| 3 | 16.7 | 16.7 | 108.4 | 100.2 |

候选zoom三轮均超过32ms，viewport产品候选未应用；pan仅沿原约定报告，旧v2初始化失败原件不回填。依据：[before](../../.runtime/r22-live-viewport-production-matrix-preparation-p-v3/evidence/run-root-before-v3-a/receipt.json)、[candidate](../../.runtime/r22-live-viewport-production-matrix-preparation-p-v3/evidence/run-root-candidate-v3-a/receipt.json)、[完整比较](../../.runtime/r22-live-viewport-production-matrix-preparation-p-v3/comparison-root-v3-a.json)。这些结果不证明GPU呈现、真实业务数据库或完整视觉一比一。

R19新1440/PNG/immediate/upload-unavailable场首次因输出父目录缺失setup **OS1/1error/13.51s**：`Path.mkdir → FileNotFoundError [WinError3]`，浏览器未启动，原件保留。新独占父目录重跑实际 **OS0/1passed/0skip/1warning/48.75s**：首次API转发0、刷新0，原Ctrl+S后同键恰1次multipart；原File/SHA、像素、永久身份、cookies-only零重传、MySQL/MinIO/HEAD/Range/三方权限全部后置通过。Root实看pending/fresh，原20guard/15服务/两源前后保持、4397监听absence和本轮MySQL cleaned已到；未做完整存活树或全资源absence。与先前回执丢失场不同，task/model POST0，不能计供应商。依据：[实际终态](../../.runtime/r19-pending-image-unavailable-live-root-v2/root-terminal.json)、[完整后置](../../.runtime/r19-pending-image-unavailable-live-root-v2/temp/test_original_browser_recovers0/acceptance.json)、[首次setup失败](../../.runtime/r19-pending-image-unavailable-live-root-v1/junit.xml)。

本片只追加维护记录，产品/ORM/DDL/依赖未新增；既有524画布测试/source check/统一build和2939后端unit/API结果保留原版本范围，未重复执行。完整媒体、正式32ms、同版部署/可信公网首帧、R23未批准、34历史unknown不重投、独立音频/企业及完整R10/M0–M5/一比一仍开放。
