# 外部资源导入与原画布媒体恢复验收

后续原画布音视频节点的离线原键恢复已补实现：16独立场分批通过，覆盖原占位
确认后及本体POST立即故障；本片六个图片/多文件媒体场在新生产版本完整回归。
新片范围、红绿批次和剩余边界见[音视频审计](2026-10-10-beeftv-pending-media-audit.md)。
下文保留本片原版本记录，不能套用其冻结结果证明后续生产前端变化；H.265、动态
资源政策、立即图片、时间线等其他离线媒体和完整并发仍未由节点恢复验收关闭。

日期：2026-10-10，接续R19。用户已授权三个Agnes免费模型真实验收；本文只记录
HTTP资源、MySQL/MinIO和浏览器媒体，不把受控故障或FFmpeg测试片当作模型验收。

## 实现与源边界

原前端已有 `importResourceFromUrl`，Python没有对应路由；首个真实HTTP测试返回405。
新增路由、Pydantic请求和独立导入Service，复用原multipart资源检查、权限及持久身份。
四份后端代码文件为 `api/v1/canvas_resources.py`、`schemas/canvas_resource.py`、
`service/canvas_resource_import.py` 和 `tests/integration/test_canvas_resource_import.py`。
另补 `frontend/canvas/scripts/verify-resource-recovery-python.mjs`，只做原UI验收。
没有DDL、新依赖、生产前端、标准模式或全局访问层改动；R23未批准候选未应用。

新增 `POST /canvas-runtime/resources/import`，接收url/kind及可选实际媒体元数据提示，
必须使用原请求键，可指定已捕获的画布。SafeTransport有界下载、DNS pinning/私网/
逐跳验证、不转发调用者凭据；下载前和归档前核验当前权限。复用Pillow/ffprobe探测，
真实原字节进MinIO，只持久化稳定ID和来源请求摘要，不保存临时URL。

同作者同键同请求的已完成资源直接重放；MinIO已写但回执丢失时，读原对象继续原
预留ID，不再远程GET。未存储时的来源变动返回409并保留预留ID，不能替换原文件。
独占导入锁仅串行同作者同键，外部下载不持有项目/上传行/用户全局锁；忙时返回409。
上限沿原multipart为50MiB，超限413并提示下载后分片。非法地址/跳转422、其他下载
故障503，公开错误与统一日志不回显URL、签名或鉴权头。

已核对固定源Go的 `ImportResourceURL`：hosted使用动态policy并调用IngestUpload，
local分支拒绝URL导入。当前画布默认local；`uploadImage`只有非local字符串输入先调
导入API，local沿浏览器fetch/文件上传。本文API测试不冒称默认原菜单已调用导入路由。
源动态大小/配额、OSS/Ark、H.265播放副本、音视频离线重传和完整R19继续未关闭。

## HTTP与存储红绿记录

| 批次 | 实际结果 | 说明 |
| --- | --- | --- |
| import-red-a | 1 failed / 8 deselected，7.23s | 原正常导入返回405，零远程GET |
| import-green-a | 2 failed / 7 passed，24.58s | 测试节点缺content；同键重叠真实返回一200一409，保留完整红证据 |
| import-green-b | **9 passed / 0 skipped，25.96s** | 只补原content准备；独占导入mutex后同键重叠两200、一资源、一次GET |
| regression-a | **36 passed / 2 deselected / 0 skipped，135.32s** | 新导入、原上传/归一化/定位值/独立副本，相邻两个browser明确未选 |
| fast-a | **2463 passed / 0 skipped，473.31s** | 完整后端unit/API，不调用业务数据库或模型 |

九项导入覆盖真实HTTP、随机MySQL/MinIO、永久ID/SHA/原字节、私人到共享发布、
Range/HEAD、来源过期后零GET、存储PUT成功但回执丢失、未存储来源变动、音频WAV/
H.264视频/二进制、下载期间撤权、同键并发及非法地址/跳转/凭据/空与非法/超限/503。
FFmpeg只制作可探测测试视频，不作为Agnes生成或源视觉质量证据。

## 原界面恢复验收

新驱动通过原隐藏文件input、原播放控件、刷新、原Ctrl+S和仅cookies新context操作；
不写页面store、不改CSS、不强制点击。图片/音频/视频原字节由测试准备，资源持久化
真实执行；上传未受理与回执丢失通过拦截控制。后置检查原字节/MinIO/SHA、资源和
素材绑定、本人/成员/外人以及Range/HEAD。六场分别为三种场景×1440/390。

- browser-red-a：1 failed / 14 deselected，38.98s。测试误要求云图空节点；源允许保存
  尚无媒体content/storageKey的上传占位。只改断言，要求不保存本地图片身份或字节。
- browser-red-b：1 failed / 14 deselected，39.77s。刷新原文件/本地键保留、显式保存
  已成功；驱动以multipart文件字节判断POST，漏计没有拦截的浏览器请求。只改驱动
  读取请求头和方法计数，没有因这两批误期望修改产品。
- browser-green-a：**6 failed / 9 deselected，312.74s**。正常三媒体逐个上传到同一中心
  导致音频按钮被视频节点遮挡；另四场浏览器complete，Python误比较含作者私人assetId
  的整节点。只改为原多文件网格和明确的共享定位/私人字段断言，不改产品。
- browser-green-b：**1 failed / 5 passed / 9 deselected，276.34s**。正常三媒体和三个
  图片恢复场全部后置通过；窄屏回执丢失场的原资源恢复成功，原占位回执尚未确认即
  刷新时触发外部改动保护，图未提交，仍保留红场；完整并发根因未据此关闭。
- browser-green-c：**1 passed / 14 deselected / 0 skipped，45.36s**。只读等待原占位
  revision回执和无在途journal后再刷新，窄屏原保存/资源/权限后置通过。未取消冲突
  保护、强制覆盖、改写journal或增加等待上限；此准备不代表全部并发恢复已验。
- browser-green-d：**2 failed / 4 passed / 9 deselected，349.87s**。正常媒体和两屏宽
  丢回执全部后置通过；立即未受理时，云图可以合法仍为空，驱动误等必有占位而超时，
  原文件/本地键均保留、external为空。只把故障暂停点放到原上传占位回执之后，
  不强制制造图状态、不延长等待、不修改产品；立即失败/并发门禁继续单独保留。
- browser-green-e：**6 passed / 9 deselected / 0 skipped，251.58s，exit0**。最终同版
  六场原UI及全部Python后置通过，使用原占位实际回执后才释放测试故障。正常三媒体
  原控件实际解码/时间推进，图片两种故障后原字节/键/资源ID、显式保存、只cookies
  fresh恢复、MinIO/原SHA、三方权限和Range/HEAD通过；六唯一组合及fresh合成图已复核。

red-a关键堆栈为 `assert.deepEqual(nodes, [])`，实际返回合法 `fileUpload: uploading`
占位；red-b为 `1 !== 2`，位于 `verify-resource-recovery-python.mjs:180`。完整日志和
失败截图保留在 `backend/.runtime/resource-recovery-browser-red-{a,b}.log` 及各独立
`*-temp/`，不覆盖旧批次。

green-b窄屏关键堆栈为原“画布保存状态：已保存”等待超时；实际失败截图显示
“画布有未处理的外部改动，本次未提交”，原文件仍可见。green-c只读核对
`confirmedRevision`等于原占位云revision且`inFlight=null`后刷新；先证明原准备已完成，
不把真正冲突当作可以自动覆盖。R21全部并发与未知提交仍有独立门禁。

green-d的两红发生在故障后要求占位存在，实际云图为空且原revision=1，浏览器原文件
SHA和本地键仍在。green-e在真实原文件POST处暂停，只读等原“uploading”占位实际
提交与journal回执，再释放503或丢失的真实200回执；不调用保存函数或注入节点。
这是明确的故障点准备，不能代替立即失败/未确认占位时刷新及全部并发的证明。

## 复现命令与未验范围

从backend运行，每批使用全新basetemp。真实媒体浏览器另需原Chrome路径与显式开关：

```powershell
$env:RUN_CANVAS_RESOURCE_MINIO='1'
.venv/Scripts/python.exe ../.runtime/beeftv-resume-run.py tests/integration/test_canvas_resource_import.py tests/integration/test_canvas_resources.py tests/integration/test_canvas_resource_normalization.py tests/integration/test_canvas_media_locators.py tests/integration/test_canvas_resource_copy.py -q --tb=short -p no:cacheprovider -k 'not browser' --basetemp=.runtime/resource-import-regression-a-temp
.venv/Scripts/python.exe -m pytest tests/unit tests/api -q --tb=short -p no:cacheprovider --basetemp=.runtime/resource-import-fast-a-temp
$env:RUN_CANVAS_BROWSER_INTEGRATION='1'
$env:PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'
.venv/Scripts/python.exe ../.runtime/beeftv-resume-run.py tests/integration/test_canvas_resource_import.py -q --tb=short --assert=plain -p no:cacheprovider -k browser --basetemp=.runtime/resource-recovery-browser-green-e-temp
```

运行日志和产物位于忽略的 `.runtime`。未在业务库执行清理，不重启用户原API/前端，
不把测试API视为同版八角色部署。供应商的桌面视频503及Responses图片400、音频/
企业授权、32ms、完整源对照和M0–M5仍保留各自门禁，goal保持active。
接续见[台账](2026-10-08-beeftv-remaining-work.md)、
[交接](2026-10-06-beeftv-resume-handoff.md)与[接口合同](../api/canvases.md#资源上传复制与读取)。

最终全仓Ruff check、本片四Python文件format、Node语法及范围内diff check通过；
全仓format仅旧未改 `scripts/apply_assembly_migration.py` 混合换行未过，752文件
已格式化，不修无关脚本。生产前端及依赖未变，没有为测试驱动/文档重复全量build。
六场的原图、原音视频为可探测夹具，不能称真实Agnes媒体生成；模型最新补验另见
[Agnes审计](2026-10-10-beeftv-agnes-live-audit.md)。

本片最终冻结与环境盘点已实际完成：12文件范围内的235个本地链接/锚点通过，
五份验收源码清单已冻结并复核，R18两份验收源码保持原SHA。MinIO保留原3桶、
测试桶为0；任务标签容器为0，4192–4197无监听；两份BeefTV源树干净且HEAD不变。
用户原API PID28840（启动UTC 2026-10-08T02:07:23.8222390Z）和前端PID28808
（启动UTC 2026-10-08T02:00:14.4573900Z）保留，没有重启。下一片音视频离线恢复
另建范围清单，不能沿用本片冻结结果证明新代码。
