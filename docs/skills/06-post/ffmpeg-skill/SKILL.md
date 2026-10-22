---
name: ffmpeg-skill
description: '用本地 FFmpeg 从自然语言请求编辑视频与音频：裁剪、修剪、拼接、缩放/重新构图（9:16、1:1）、变速、字幕（SRT/ASS，动画、卡拉OK）、Logo 与文字叠加、下三分之一字幕条与标题、静音去除、多机位与外接麦克风同步、响度归一化、HDR/杜比视界转 SDR、LUT、带闪避的背景音乐、平台导出（YouTube、Reels、TikTok、X）、合规检查、场景检测与高光集锦、用于检查结果的缩略图网格，以及整段编辑的工程文件。每当用户提到视频或音频文件（mp4、mov、mkv、wav、m4a）、素材、片段、字幕、短视频、YouTube/Instagram/TikTok 交付、LUFS、同步、转码、ffmpeg，或要求把某个东西做成“60 秒”、“竖屏”、“更响”、“加字幕”——即便他们没说“编辑”——都应使用本技能。仅依赖 Python 3.9 标准库，无云端、无 API 密钥。'
---

# ffmpeg-skill

脚本与本文件同级的 `scripts/` 目录下，用 `python3 <技能目录>/scripts/<名称>.py` 运行；交付模板在 `templates/`。此文件已足够干活：下表负责路由请求，而准备运行的脚本上的 `--help` 是最便宜的完整参数清单。参考文件的成本与此文件相当；只在遇到具体问题时再打开一个：`references/scripts.md`（全部 42 个脚本的每一个参数）、`references/devices.md`（iPhone HDR、GoPro、DJI、录屏、Zoom）、`references/gotchas.md`（末尾那一行行规则的详尽版）。MCP：`tools/list` 默认只显示核心 12 个；其余 30 个（按此表）仍可通过 `tools/call` 按名称调用，由 `contract --json` 描述；把 `FFMPEG_SKILL_MCP_FULL=1` 设为全部 42 个。

共享参数（每个脚本都有）：`--dry-run`（空跑）；`--json`（输出路径、其探测信息、所运行的命令）；`--json-brief`（status/output/verified + 一段 `summary`；写步骤时优先用）；`--fast`（预览质量）；`--progress`（百分比/ETA）；`--timeout 秒数`（`kind: timeout`，默认 1800）；`--overwrite`（第 7 步）；`--plan 文件`（一次空跑的 `render.py 文件` 稍后运行；若输入有变动则拒绝）。重新编码工具还接受 `--hw`（Apple Silicon GPU，大文件；`FFMPEG_SKILL_HW=1` = 默认，不含导出）、`--codec h264|hevc|av1|prores` 与 `--quality N`：不设时，SDR 为 x264（`cut.py --keep-hevc`：HEVC 源保持 HEVC），HDR 为 x265 Main10；`prores` 需要 `-o 名称.mov`，`h264` 拒绝 HDR（先 `color.py --to-sdr`）。

写工具在 `--dry-run` 下不运行任何东西；测量工具（`probe`、`check`、`sync`、`multicam`、`scenes`、`cropdetect`、`report`、`silence`、`loudness`、`stabilize`）可能仍会运行 ffmpeg/ffprobe，但跳过产物/附带文件（`--edl`、`--sheet`、生成的 `.ass`）；`verify` 忽略该标志。按工具：`contract --json` `dry_run`。

## 工作流（按此顺序）

0. **环境，仅在失败时。** 绝不要用 `doctor` 启动任务：一台坏掉的机器会以 `kind: missing_tool` 或一个点名滤镜/编码器的 ffmpeg 错误失败。在那样的失败后，或在被问到这台机器能做什么时，运行 `python3 <技能目录>/scripts/_contract.py doctor`（或 `npx ffmpeg-skill doctor`）：读取 `ok`/`usable`，报告缺失的能力。`contract --json` 的工具 schema 是给*规划*智能体用的，不是给本工作流用的。
1. **探测你规划所必需的东西。** 对每个你要据以规划的输入运行 `probe.py`——时长、fps、分辨率、编码、声道、`variable_frame_rate_suspected`——以及每当用户问起某个文件时。不必在每次编辑前都单独探测：每个写工具的 `--json` 已经带着它的输入与输出的探测。从真实数字规划，而非假设。
2. **优先无损。** 如果请求无需重新编码即可满足（关键帧上的纯裁剪、重封装、仅改音频），就不要重新编码。`cut.py` 与 `loudness.py` 默认视频流复制；`cut.py` 的 `--accurate` 仅用于逐帧精确的裁剪。
3. **先用 `--dry-run --json` 规划，再执行。** 规划中的任何数字都信任 `--json`，而非空跑的摘要行（那里的某个维度可能是占位符）。在长编码前、要报告事实时使用。除非用户要求替换，绝不要把 `-o` 指向一个你在本任务中没创建的文件；只有那时才传 `--overwrite`。
4. **按合理顺序串接。** 一个不含其他编辑的交付请求是一次模板运行（`render.py --template 名称 输入`），而非手工搭建的链。否则：调色（HDR→SDR / LUT）→ 裁剪 → 拼接 → 静音 → 适配 → 字幕/叠加 → 同步 → 音频 → 响度 → 导出。帧变化在字幕之前，这样文字是按最终帧尺寸排的。尽量少重新编码：中间产物用 CRF 18，`export.py` 最后跑。**三步及以上：`render.py` 配一个 project.json。**
5. **检查交付物。** 在报告前，对所指目的平台运行 `check.py 输出 --platform X`（模板运行已经做了）。格式行（编码、像素格式、尺寸、真峰、色彩标签、VFR）可以安全修复。判断行改变内容：时长（裁剪丢失素材）、画幅（裁切丢失边缘）、fps（丢帧）、响度（环境音绝不能被抬高）——只有在请求暗示答案时才修复，否则说明选择及其代价。提及 WARN，但别去追逐它们。
6. **验证输出。** 从写工具的 `--json`/`--json-brief` 探测或 `probe.py`，确认时长、分辨率、fps 与音频符合请求，并报告这些数字（"final.mp4：59.98 秒、1080x1920、30 fps、AAC 立体声"）。只有当脚本退出码为 0、且输出探测符合预期时，一个步骤才算完成：非零退出、缺失或空文件，或与请求矛盾的探测，都是要用脚本错误信息报告的失败。
7. **保留用户的原片。** 绝不要覆盖源文件；把新文件写到输入旁边或用户指定的位置。一个已存在的输出路径会被拒绝（`kind: input`）；`--overwrite` 是唯一的"是的，替换它"开关。
8. **看画面。** 每当画面变了（字幕、叠加、图形、裁切/补边、缩放、调色、转场，或 `join.py` 把一段缩放到第一段帧、`color.py --to-sdr`），运行 `look.py 输出 --tiles 3x2`（或 `--at T` 取单帧）并查看 PNG（整段布局用 4x3）。在 `Look:` 点名那张 PNG 之前不算完成——探测看不出盖在脸上的字幕。纯音频：`Look: 不需要`。要看的东西像第 5 步 `check.py` 的行那样拆分：
   - **机械性（本技能自己的活，负责验证并报告）：** 指定的文字/Logo 在指定位置，字幕出现在指定时间戳，尺寸为偶数。`fit.py --fit pad` 的上下黑边是该模式的*正确*结果，绝不是要标记的缺陷。
   - **判断性（报告它，不悄悄通过或判失败）：** 主体或脸是否被切掉、文字是否压在脸上、颜色是否发灰、转场是否落地。这些需要判定主体*是什么*，是调用智能体的决定——用一句话说你看到的；由他们判断。
   在没有视觉能力时，写 `Look: 路径（像素未检查；智能体无图像视图）`——绝不声称看过了没看的画面，也不要为了一个不存在的能力而停顿。

## 运行任何东西之前：问什么、假设什么

只有当答案会实质改变输出、且请求并未暗示它时，才问一个简短的问题。当一次有多个东西敞着（一句模糊的"做成社媒用的"留下目的、画幅、时长与字幕都未定），不要每轮问一个：提出一个带默认值的打包方案，让用户改任何部分（"Reels：9:16 补边、60 秒、-14 LUFS、无字幕——可以吗？"）。一个问题、一个答案，然后开跑。绝不要问 `probe.py` 能告诉你的东西。

- **目的平台**决定画幅、时长上限、响度与编码；"做成 Reels"一次性回答四个并点名一个模板。无目的平台且只是纯裁剪/字幕：保持源格式并说明。如果用户说"导出""发布"或"交付"，问去哪里。
- **时长**（"做成 60 秒"）未给方法：≤1.5× 的变化用加速，否则用修剪，并说明哪种。当内容是谈话（修剪丢字）、且变化很大时问。
- **字幕**无文本来源：`--transcribe` 若有本地引擎则用，否则要文本或带时间的文件；绝不要编造对白。
- **字体与品牌**：如果用户提到品牌、颜色或"我们的字体"，一次性要或创建 `brand.json`，复用它。
- **CJK / 非拉丁文字**：让工具按文字系统挑字体（`--font` 关闭它）；汉字-only 文字用 `--lang ja|ko`。`doctor --json` 的 `.fonts.scripts` 说明这里能渲染什么。豆腐块（tofu）是失败的活。
- **`--fit crop` 的裁切位置**：默认居中，但当请求或源点名一个偏离中心的主体（"把产品留在右侧"、"在预览表里明显偏一侧的人"）时，用 `--crop-x`/`--crop-y`（0=左/上，1=右/下）而非猜居中，或问保留哪条边。
- 其他（转场类型、字幕风格）：挑常规默认，说明你挑了什么，用一句话给出替代方案。

## 本技能决定什么、不决定什么

本技能裁剪、拼接、测量、同步、导出、检查文件——它执行一次编辑，不决定一次编辑。属于人、调用智能体或其他技能的事：

- **哪一刀是对的，或交付物是否可过审**——本技能测量并报告（`check.py` 的 PASS/WARN/FAIL、`cut.py` 的时长错误）；由用户或制作智能体决定它是否上线。
- **什么让高光有趣**——`scenes.py --highlights` 按一个测得的代理（音频能量、时长）排序：是候选，不是结论。
- **缩略图或封面构图**——设计决定，不是测量。
- **理解视频*关于*什么**——除了 `look.py` 的缩略图网格供调用智能体亲眼看，这里没有视觉；也不是给本技能去解读的。
- **评判什么好看**——"套用这个 LUT"、"曝光 +0.3 档校正"（`color.py`）是机械的；"把这个场景调出电影感"属于一个调色技能（[`color-grading-skill`](https://github.com/kajisho5/color-grading-skill)），由它决定参数，再调用 `color.py`。
- **字幕里的文字**——字幕文本按所写烧录。对画面太长意味着更小的字号、`--max-lines`，或用户自己的编辑；绝不要改写、缩短或转述它，即便被要求"让它装得下"——如实说明，提供小字号下的 `--max-lines 1`。
- **挑一个你没被告知的主体或区域**——"裁到 x=200,y=0"一旦框定了就是机械的；"裁切以让说话人留在画面里"需要先判定*说话人是谁*，是调用智能体的判断（来自 `look.py` 的预览表）。

边界线：相同的输入 + 相同的显式参数，总是产生相同的可验证输出，才归这里；任何取决于品味、理解或"看起来/听起来好"的东西，归做出该判断的人。

如果一个请求需要 42 个脚本都未暴露的 FFmpeg 功能，如实说明并指出最接近的內置选项——绝不要在 `scripts/*.py` 之外猜测原始的 `ffmpeg`/`ffprobe` 调用。那会绕过本技能作出的每一项保证，因此绝不作为退路。

## 请求 → 脚本

此表与 `doctor --json` 的 `tools` 列表是"有什么"的事实来源：只点名你在其中一个里见过的脚本（没有 `doctor.py`、没有 `trim.py`、没有 `subtitle.py`）。

时间戳参数（`--start`、`--end`、`--at`、`--from`、`--duration`、`--offset`，以及字幕与章节文件里的时间）接受秒、`mm:ss(.fff)`、`hh:mm:ss(.fff)`，或带 `@fps`（`00:01:02:15@29.97`）的 SMPTE `hh:mm:ss:ff`——原样粘贴 NLE 的打点表即可；长度参数（`--min-silence`、`--margin`、`--fade`）是纯秒。

| 用户说 | 做 |
|-----------|----|
| "从 1:20 剪到 2:05"、"修剪前 10 秒" | `cut.py input.mp4 --start 1:20 --end 2:05` |
| "只留这些部分"、"去掉中间" | `cut.py input.mp4 --segments 0-1:00,1:30-2:00` |
| "精确做成 60 秒" | `fit.py input.mp4 --duration 60`（变速）或 `--method trim` |
| "剪这个并做成 HEVC / AV1 / ProRes"（指名输出编码） | `cut.py input.mp4 --start 0:10 --end 0:40 --codec hevc`（ProRes 需要 `-o 名称.mov`） |
| "剪掉停顿"、"跳切" | `silence.py input.mp4 [--threshold -40 --min-silence 0.8]` |
| "剪掉嗯啊"、"去掉 filler 词" | `silence.py input.mp4 --filler --words words.json`（测得词的时间；`--transcribe` 生成它们） |
| "别在句子中间剪，只剪真正的停顿" | `silence.py input.mp4 --speech-aware`——句子内低于 `--min-silence` 的呼吸保留，只剪句子边界的停顿；可与 `--filler` 合并成一份清单 |
| "从这小时里做 60 秒高光" | `scenes.py long.mp4 --highlights 6 --target 60 --edl picks.txt` → `cut.py --segments` |
| "踩着节拍剪"、"跟着音乐剪" | `scenes.py track.mp4 --beats --json > beats.json`，然后 `cut.py input.mp4 --segments ... --snap beats --snap-source beats.json`（`--snap-source` 把测得的网格带过去） |
| "这里加速、那里慢动作"（已知片段） | `speedramp.py action.mp4 --segment 0-3:1.0 --segment 3-4:0.25 --segment 4-8:2.0` |
| "定格这帧"、"冻结尾帧" | `freeze.py clip.mp4 --hold 2` |
| "流畅慢动作"、"半速但顺" | `fit.py input.mp4 --duration 2x --smooth interpolate`（慢）或 `--smooth blend` |
| "反转这条片段" | `reverse.py input.mp4` |
| "循环这条片段填满 30 秒" | `loop.py bg_loop.mp4 --duration 30` |
| "做成竖屏 / 9:16 / 方形" | `fit.py input.mp4 --aspect 9:16 --fit pad`（或 `--fit crop`；`--pad-fill blur` 用于模糊边条） |
| "缩放到某个高/宽" | `fit.py input.mp4 --height 1080`（或 `--width`，或两者都给以得到精确帧） |
| "裁到这个精确框"（已知 x/y/w/h） | `crop.py input.mp4 --x 100 --y 0 --width 1080 --height 1920` |
| "用模糊背景代替黑边" | `fit.py input.mp4 --aspect 9:16 --fit blur`（整图保留，边缘是模糊、压暗的副本） |
| "旋转 90 度"、"镜像它" | `fit.py input.mp4 --rotate 90` / `fit.py input.mp4 --flip h` |
| "画面歪了"（已知度数） | `straighten.py tilted.mp4 --degrees -2.5` |
| "从这个 360 视频里拉出平面视图"（已知 yaw/pitch/fov） | `sphere.py insta360.mp4 --yaw 90 --pitch 0 --h-fov 100 --v-fov 70` |
| "这段老素材是隔行扫描的" | `deinterlace.py input.mp4` |
| "有颗粒/噪点，清理一下" | `denoise.py input.mp4 --strength medium` |
| "模糊/马赛克这张脸/这版"（已知框） | `redact.py input.mp4 --x 820 --y 140 --width 240 --height 240 --mode pixelate` |
| "去掉这个固定 Logo"（已知框） | `redact.py input.mp4 --x 40 --y 20 --width 180 --height 60 --mode delogo` |
| "稳定这段抖动素材" | `stabilize.py input.mp4` |
| "这上面有黑边吗" | `cropdetect.py input.mp4` |
| "iPhone 杜比视界片段看着不对" | `color.py clip.mov --to-sdr` 或 `--strip-dovi`（保留 HDR，丢掉 DV 层） |
| "颜色发灰 / iPhone HDR" | `color.py input.mov --to-sdr`（探测显示 `hdr: true`） |
| "套用这个 LUT"、"转换 S-Log 素材" | `color.py input.mp4 --lut grade.cube [--lut-strength 0.7]` |
| "颜色标签标错了" | `color.py input.mp4 --retag bt709`（流复制；仅在复制带不动时才重新编码——见 `reencoded`） |
| "提亮 / 拉对比 / 校正白平衡" | `color.py input.mp4 --correct --exposure 0.3 --contrast 1.1 --saturation 1.05 --temperature 5600 --tint -0.05` |
| "用这个 SRT 加字幕"、"烧录字幕" | `caption.py input.mp4 --srt subs.srt` |
| "用这些行加字幕"（带时间的文本） | `caption.py input.mp4 --text cues.txt` |
| "保留可开关的字幕"、"封装进一个 SRT" | `caption.py input.mp4 --srt subs.srt --mode mux`；多语言重复 `--srt 文件:语言`，超过两种用 `.mkv` |
| "字幕太小 / 短视频上三行"、"别把句子截断" | `caption.py` 在拆分前把字号缩到字幕装得下 `--max-lines`（`--fit-size off` 保留 `--size` 并改为拆分，`--min-size` 为下限）。保留模板的 `--max-lines`（竖屏上为 2），让字号降下来；抬高它来躲缩小，会让每行挤两个词。一个词在下限处仍比栏宽，就在边缘切（优先连字符），绝不改写，自动进行；JSON 里的 `broken_inside_word` 计数它 |
| "转写并加字幕" | `caption.py input.mp4 --transcribe --animate pop --karaoke`（Parakeet/whisper；否则 `--text`） |
| "TikTok 风格、词蹦出来的字幕" | `caption.py input.mp4 --text cues.txt --animate pop --karaoke` |
| "我们的 Logo 右上"、"水印" | `overlay.py input.mp4 --image logo.png --position top-right --scale 200` |
| "开头 4 秒的标题" | `overlay.py input.mp4 --text "标题" --position top --start 0 --end 4 --fade 0.4` |
| "角落的摄像头片段"、"画中画" | `overlay.py input.mp4 --video webcam.mp4 --position bottom-right --scale 480` |
| "去掉绿幕" | `overlay.py bg.mp4 --video greenscreen.mp4 --chromakey 0x00ff00` |
| "下三分之一字幕条带我的名字"、"倒计时片头"、"进度条" | `graphics.py input.mp4 --template lower-third --name "..." --title "..." --start 2 --end 8` |
| "贴纸"、"前 3 秒的钩子卡"、"表情包文字" | `graphics.py input.mp4 --template sticker --text "NEW" --platform tiktok` / `--template hook --title "..." --duration 3` / `--template meme --top "..." --bottom "..."` |
| "用我们的品牌字体/颜色/Logo" | 在 caption/overlay/graphics 上加 `--brand brand.json`，或在 project.json 里写 `"brand"` |
| "同步领夹麦"、"对齐两台相机" | `sync.py camera.mp4 mic.wav --replace-audio` / `sync.py camA.mp4 camB.mp4 --trim-second`；第三台及以上录音是另一个位置参数（`sync.py ref.mp4 mic.wav cam2.mp4`），一个偏移 JSON |
| "修正音频电平"、"归一化到 -14 LUFS" | `loudness.py input.mp4`（`-I -16 --tp -1.5` 播客，`-I -23` 广播；`--lra N` 设范围） |
| "清理音频"、"去掉嘶嘶声" | `audio.py input.mp4 --voice`（人声；`--voice light\|medium\|strong`）或 `--denoise` |
| "在说话声下加背景音乐" | `audio.py input.mp4 --music bed.mp3 --duck --fade-out 3`（`--effects sfx.wav` 加第三层，绝不闪避；工程电平：`audio.stems`） |
| "让音乐闪避更狠 / 回来更快" | 加 `--duck-amount 18 --duck-threshold -30 --duck-release 250`（`--duck-attack` 也行） |
| "混音听着窄"、"更宽的立体声" | `audio.py band.wav --stereo-widen 0.5`（需要真正的立体声源；5.1 需要 `--downmix`） |
| "把 5.1 转成立体声" | `audio.py input.mov --downmix` |
| "换上旁白轨" | `audio.py input.mp4 --replace narration.wav` |
| "抽出音频"、"给我 WAV 声音" | `audio.py input.mp4 -o input.wav`（音频扩展名会丢掉画面；`--audio-stream 1` 选一条轨） |
| "压缩人声"、"限制峰值"、"门控房间噪声" | `audio.py input.mp4 --compress --comp-threshold -20 --comp-ratio 4` / `--limit --limit-ceiling -1` / `--gate --gate-threshold -45` |
| "音频在一小时里逐渐失步" | `sync.py camera.mp4 recorder.wav --fix-drift --replace-audio` |
| "把播客做成视频"、"audiogram" | `render.py --template audiogram ep.m4a --image cover.png`——波形压在静帧或色块上；不抓取任何东西 |
| "做成 TikTok / Reel / Short / YouTube / X / LinkedIn / 播客" | `render.py --template tiktok\|reels\|shorts\|youtube-shorts\|youtube\|x\|linkedin\|facebook\|podcast input.mp4 [--cues cues.txt] [--title "..."]`——画幅、字幕、响度、导出、检查一条命令搞定（`--list-templates`；`--write-project` 先编辑） |
| "发到所有平台"、"每个平台一个版本" | `render.py --template all input.mp4 --cues cues.txt`（或逗号列表）→ 每个目的地一个文件，外加 `<名称>_pack.md`（`report.py --pack` 渲染 HTML） |
| "为 YouTube / Reels / X 导出"、"一个 ProRes 母版" | `export.py input.mp4 --preset youtube\|reels\|tiktok\|shorts\|linkedin\|facebook\|x\|prores\|h265`（`--normalize` 在同一次调用里命中响度规格；`youtube-hdr` 保留 HDR，`youtube-av1` 写 AV1） |
| "做 GIF 预览" | `export.py input.mp4 --preset gif` |
| "一个小的代理 / 廉价预览文件" | `proxy.py input.mp4 [--width 640 --no-audio]`——不是交付预设（那是 `export.py`） |
| "这个能上传吗" | `check.py final.mp4 --platform reels` |
| "带章节的播客单集" | `loudness.py ep.wav -I -16 --tp -1.5` → `metadata.py ep.m4a --chapters chapters.txt` → `check.py ep.m4a --platform podcast`（章节与声道行） |
| "发给我一份你做了啥的总结" | `report.py --before raw.mov --after final.mp4 --platform youtube -o report.html` |
| "把这张图变成片段"、"标题卡" | `insert.py title.png --duration 3` |
| "照片缓慢推近"、"Ken Burns" | `insert.py photo.jpg --duration 6 --zoom in --pan right --width 1920 --height 1080` |
| "做一个空白/纯色背景片段" | `background.py -o bg.mp4 --duration 3 --width 1920 --height 1080 --color 0x101010` |
| "把这些编号帧做成视频" | `sequence.py --dir frames --pattern "frame_%04d.png" --fps 24` |
| "这段音轨的波形/频谱视频" | `waveform.py podcast.wav -o waveform.mp4` |
| "开头加黑场" | `pad.py clip.mp4 --start 1.5` |
| "切到产品镜头 0:12-0:16"、"这段上 B-roll" | `broll.py talk.mp4 --insert product.mp4 --at 12 --end 16`（重复 `--insert/--at`；`--audio b\|mix`） |
| "加章节"、"YouTube 章节标记" | `metadata.py episode.mp4 --chapters chapters.txt`（每行 `时间 标题`；流复制；工程里写 `"chapters"`）。`--auto-chapters` 从测得的停顿/场景切分提议它们（`Chapter N`） |
| "设标题 / 艺术家 / 注释" | `metadata.py episode.mp4 --title "第 12 集" --artist "工作室"` |
| "把这些视频排成 4x2 网格" | `grid.py t1.mp4 ... t8.mp4 --cols 4 --rows 2` |
| "拼接这些片段"、"加交叉淡化" | `join.py a.mp4 b.mp4 c.mp4 --transition fade --duration 0.5`（TTS：`--list parts.txt`、`--on-missing skip`、`--on-silent fail`） |
| "一次编辑多处改动"、3 步及以上 | `render.py --init project.json`，编辑，`render.py project.json` |
| "我改了一处，别重做其余" | `render.py project.json --cache 目录`——相同的阶段复用（`--from 阶段` 从那里开始） |
| "在 Premiere / Resolve / Final Cut 里打开它" | `render.py project.json --export-timeline edit.fcpxml\|.edl\|.otio`——不渲染任何东西；读 `not_exported`；没有工程？`--write-project` |
| "对文件夹里每个文件做"、"用满所有核心" | `batch.py 文件夹 --recipe batch.json --jobs auto`（步骤或一个 render 工程；已缓存） |
| "三台相机，在它们之间切换" | `multicam.py camA.mp4 camB.mp4 camC.mp4 --switch "0-20:0,20-40:1,40-60:2"`（手动）或 `--switch energy`（`--min-shot`）；`--edl` = 剪辑清单；编辑器：`--write-project p.json` → `--export-timeline` |
| "这文件里有什么"、"多长" | `probe.py input.mp4` |
| "让我看看它长啥样"、"字幕可读吗" | `look.py output.mp4 --tiles 3x2`，然后查看 PNG |
| "你会跑什么"、"先别渲染" | 任何带 `--dry-run` 的脚本 |
| "哪些镜头是静止 vs 运动"、"每秒音量峰值"、"这是说话还是音乐" | `scenes.py input.mp4 --shots`（每个镜头的静止/平移/运动，测得的流） / `--audio-peaks`（dBFS 列表） / `--speech`（说话对音乐的比值，不是分类）——组合或单独用 |
| "主体往哪动，好让我自己裁" | `cropdetect.py input.mp4 --motion-centre`——每秒运动质心，仅报告；由调用智能体挑裁切 |
| "看着像 Log / S-Log 吗" | `probe.py clip.mp4 --analyze`（`looks_like_log`）然后 `color.py --lut` |
| "用我的真实文件测这个工具" | `verify.py ~/Footage --report verify.md` |
| "给我看进度"、"先来个快速预览" | 任何编码脚本加 `--progress` 和/或 `--fast` |
| "这是手机拍的可变帧率视频" | 无需额外：重新编码把 VFR 规整为恒定 fps；`fit.py --fps 30` 选速率 |

## 仅音频文件

音频是一等公民输入：`probe`、`cut`、`silence`、`loudness`、`audio`、`sync`、`check --platform podcast`、`render --template podcast` 接受 WAV、FLAC、MP3、M4A/AAC、OGG、Opus；输出扩展名决定格式。需要画面的脚本（`fit`、`caption`、`overlay`、`graphics`、`color`、`export`、`scenes`、`look`）会以"输入没有视频流"拒绝音频——别硬套视频封装。配方见：`references/gotchas.md#audio-only-files`。


## 报告格式

用请求所用的语言回复——另一语言的字幕仍用请求的语言报告。保留标签（`Done:`、`Steps:`、`Check:`、`Look:`、`Notes:`）为英文；其余都是用户的语言。绝不要因为活短或失败就偏离：哪怕一行"文件不存在"。对话中途切换跟随用户最新的消息。

用这个形状结束每个任务（数字来自 `--json` 或 `probe.py`/`check.py`，而非记忆）：

```
Done: final.mp4 — 59.98 s, 1080x1920, 30 fps, H.264, AAC stereo, -14.1 LUFS
Steps: cut 0:12-1:12 (lossless) -> fit 9:16 crop -> captions (pop, karaoke) -> loudness -14 -> export reels
Check: reels — all 12 checks pass (verified: true)
Look: final_sheet.png (captions inside the safe area, logo top-right)
Notes: source was VFR, conformed to 30 fps; audio was mono, made stereo
```

同一个日请求的五行：

```
Done: final.mp4 — 59.98 秒、1080x1920、30 fps、H.264、AAC ステレオ、-14.1 LUFS
Steps: 0:12-1:12 をカット（無劣化）-> 9:16 にクロープ -> 字幕（ポップ、カラオケ）-> ラウドネス -14 -> Reels 書き出し
Check: reels — 12 項目すべて合格（verified: true）
Look: final_sheet.png（字幕はセーフエリア内、ロゴは右上）
Notes: 元は VFR だったので 30 fps に揃えた。音声はモノラルだったのでステレオにした
```

保持这五行，加上任何用户必须决定的东西。绝不在没有输出探测的情况下报告成功；绝不要描述你没跑过的修复。

当某一步失败时，把 `Done:` 换成 `Failed:` 并诚实保留其余：

```
Failed: color.py --lut grade.cube exited 1 — ffmpeg: "Unable to parse LUT file" (the .cube is not a valid LUT)
Steps: probe -> color (failed); nothing written
Check: nothing to verify
Look: not needed
Notes: send a valid .cube, or say if you want the clip left as is
```

那些填充行是句子，不是标签：同一个报告对日请求以 `Check: 検証するものなし` / `Look: 不要` 结尾。

拒绝用同样的形状：`Failed:` 点名被拒绝的是什么及为什么，`Steps:` 列出确实跑了什么，`Look: not needed`。部分结果是 `Done:` 并把缺口写进 `Notes:`；一个仍交付了什么的拒绝是 `Failed:`——绝不要像 `Done (partially):` 这样的第三个标签。在 `Notes:` 里引用失败的 `error.hint`。

一次失败的 `--json` 是 `status: failed` 加 `error.kind` 与 `error.message`；引用 message，绝不转述它。

## 看着对其实是错的东西

各一行；当活在那一块时打开对应的 `references/gotchas.md` 小节。

- HDR（iPhone、HDR10）经由 SDR 路径重新编码会变平；脚本保留 HDR；`hdr: true` 是真正的 PQ/HLG/DV 信号，`bt2020_or_hdr: true` 也含 BT.2020 SDR。-> [#hdr-and-colour](references/gotchas.md#hdr-and-colour)
- Log 素材（S-Log/V-Log/C-Log）标为 SDR 且看着发灰：先 `probe.py --analyze`，然后 `color.py --lut`。-> [#log-footage](references/gotchas.md#log-footage)
- 一次 `-c copy` 裁剪可能从错误/冻结的帧开始；`cut.py` 会重新编码越过 0.5 秒吸附，尊重它；`--edit-list` 隐藏一个 .mp4 的前置帧。-> [#keyframe-cuts](references/gotchas.md#keyframe-cuts)
- VFR 手机/录屏片段：重新编码走 CFR；`cut.py` 重新编码（`--vfr-guard sampled`：仅测得的 VFR）；奇怪的速率：`fit.py --fps`。-> [#variable-frame-rate](references/gotchas.md#variable-frame-rate)
- 同步/多机位 `confidence` 低于 0.3（或巨大偏移）可疑——检查每一台相机；这些对齐的是音频，绝不是口型同步。-> [#sync-multicam-and-drift](references/gotchas.md#sync-multicam-and-drift)
- "归一化"的音频仍可能削波（检查真峰）；-40 LUFS 及以下的环境音绝不能被抬高到语音目标。-> [#loudness-and-ambience](references/gotchas.md#loudness-and-ambience)
- 在裁切/缩放之前烧录的字幕会落到画外；先烧小再被 `export.py` 放大的会发虚。-> [#captions-fonts-and-text-order](references/gotchas.md#captions-fonts-and-text-order)
- Emoji 需要 `--emoji-assets 目录`（每个字形一张 PNG）才能彩色渲染；没有则出来单色，会报告。-> [#emoji](references/gotchas.md#emoji)
- 非拉丁文字按文字系统挑字体（`graphics.py` 通过 libass 排天城文、孟加拉文、泰米尔文、泰文；drawtext 不能）；没有字体 = 失败的活。-> [#fonts-by-script](references/gotchas.md#fonts-by-script)
- `--fit crop` 16:9→9:16 扔掉 70% 宽度；"60 秒"用变速还是用修剪是不同的答案——说明哪个、为什么。-> [#reframing-fps-and-duration](references/gotchas.md#reframing-fps-and-duration)
- TikTok/Reels 用自己的 UI 盖住底部五分之一与右栏——模板让文字避开这些区；`look.py --safe tiktok` 显示它们。-> [#platform-safe-zones](references/gotchas.md#platform-safe-zones)
- `scenes.py --highlights` 按响度（或时长）排序，绝不按意义：信任选取前先看预览表。-> [#highlights](references/gotchas.md#highlights)
- CPU 重新编码：x264 `medium`；3 步以上串进一个 `render.py` 工程。-> [#chaining-and-speed](references/gotchas.md#chaining-and-speed)
