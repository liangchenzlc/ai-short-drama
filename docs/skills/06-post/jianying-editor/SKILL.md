---
name: jianying-editor
description: 剪映 (JianYing) AI自动化剪辑的高级封装 API (JyWrapper)，提供开箱即用的 Python 接口，支持录屏、素材导入、字幕生成、Web 动效合成及项目导出。全面适配 MacOS (Apple Silicon/Intel) 与 Windows，支持 v5.9+ (draft_info.json) 架构、工程自修复、智能配音字幕及录屏变焦。
---

# 剪映编辑器技能（JianYing Editor）

当用户想在剪映专业版（JianYing Pro）中自动化视频剪辑、生成草稿或处理媒体素材时，使用本技能。

智能体执行手册：[docs/agent-playbook.md](docs/agent-playbook.md)
最简命令 SOP：[docs/minimal-command-sop.md](docs/minimal-command-sop.md)
自然语言使用指南：[usage.md](usage.md)
草稿检查 CLI：
`python <SKILL_ROOT>/scripts/draft_inspector.py list --limit 20`
`python <SKILL_ROOT>/scripts/draft_inspector.py summary --name "DraftName"`
`python <SKILL_ROOT>/scripts/draft_inspector.py show --name "DraftName" --kind content --json`
对于通用剪辑请求，始终遵循该手册中的"快速编辑运行时模板"和"验收清单"。

## 🚨 重要开发原则（开发者必读）

1.  **脚本位置**：**禁止在技能内部目录创建剪辑脚本**。所有的剪辑逻辑实现代码（`.py` 脚本）必须存放在用户当前项目的**根目录**（或子目录，如 `scripts/`），以保持技能库的纯净和可移植性。
2.  **版本与架构**：
    - **双平台适配**：已全面支持 MacOS（路径探测/录屏）与 Windows。
    - **自动修复（Auto-healing）**：支持 v5.9+（`draft_info.json`）。若草稿损坏或版本冲突，使用 `overwrite=True` 初始化 `JyProject` 可触发自动修复。
3.  **配乐选择**：
    - **简单演示使用默认音乐**。实际项目，应优先检索并推荐 `data/cloud_music_library.csv` 中的相关曲目，或根据视频主题（如"科技"、"温暖"）进行关键词过滤。
    - 询问用户："我发现了几首符合主题的云端音乐，要不要试试？（如：`Illuminate` - 科技感）"。

## 规则指南（Rules）

针对具体任务和约束，阅读对应的规则文件：

- [rules/setup.md](rules/setup.md) - **必需**：所有脚本的初始化代码。
- [rules/core.md](rules/core.md) - 核心操作：保存、导出与草稿管理。
- [rules/cli.md](rules/cli.md) - CLI 契约与机器可读输出规范。
- [rules/media.md](rules/media.md) - 导入素材与 **AI 视频分析优化（30 分钟/360p）**。
- [rules/text.md](rules/text.md) - 添加字幕、文本与说明文字。
- [rules/keyframes.md](rules/keyframes.md) - **进阶**：添加关键帧动画。
- [rules/effects.md](rules/effects.md) - 搜索并应用滤镜、特效与转场。
- [rules/recording.md](rules/recording.md) - **新增**：录屏与智能变焦自动化。
- [rules/web-vfx.md](rules/web-vfx.md) - 进阶：Web 转视频生成。
- [rules/generative.md](rules/generative.md) - 生成式剪辑的思维链。
- [rules/audio-voice.md](rules/audio-voice.md) - **新增**：TTS 配音与 BGM 来源。

## 🎯 智能体快速路由（Agent Quick Routing）

- 云端视频 + 云端音乐：`rules/media.md` + `rules/audio-voice.md` -> `examples/cloud_video_music_tts_demo.py`
- 智能配音与字幕（脚本转视频）：`rules/text.md` + `rules/audio-voice.md` -> 核心 API `add_narrated_subtitles`
- 旁白与字幕对齐：`rules/text.md` + `rules/audio-voice.md` -> `examples/cloud_video_music_tts_demo.py`
- 录屏与智能变焦：`rules/recording.md` -> `tools/recording/recorder.py`
- 批量导出/无头导出：`rules/core.md` + `rules/cli.md` -> `examples/robust_auto_export.py`
- 影视解说生成：`rules/generative.md` -> `scripts/movie_commentary_builder.py`

## 📖 经典示例（Examples）

完整工作流请参考这些示例：

- [examples/my_first_vlog.py](examples/my_first_vlog.py) - 一个带背景音乐与动画文字的完整 vlog 创作示例。
- [examples/simple_clip_demo.py](examples/simple_clip_demo.py) - 基础剪切与轨道管理的快速上手教程。
- [examples/compound_clip_demo.py](examples/compound_clip_demo.py) - **新增**：专业嵌套工程（复合片段）自动化。
- [examples/cloud_video_music_tts_demo.py](examples/cloud_video_music_tts_demo.py) - 云端视频 + 云端 BGM + TTS/字幕对齐。
- [examples/web_to_video_intro_demo.py](examples/web_to_video_intro_demo.py) - Web 转视频片头示例（HTML 动画 -> 时间线片段）。
- [examples/robust_auto_export.py](examples/robust_auto_export.py) - 稳定导出工作流与失败处理。
- [examples/auto_exposure_align_demo.py](examples/auto_exposure_align_demo.py) - CV 辅助的曝光对齐工作流。
- [examples/video_transcribe_and_match.py](examples/video_transcribe_and_match.py) - **进阶**：AI 驱动工作流（视频转写 -> 通过 AI 语义匹配 B-roll -> 组装草稿）。

## 🧠 提示词与集成工具（Prompts & Integrated Tools）

用以下模板与脚本处理复杂任务：

- **素材搜索（Asset Search）**：按中文/英文名称查找滤镜、转场与动画：
  ```bash
  python <SKILL_ROOT>/scripts/asset_search.py "复古" -c filters
  ```
- **影视解说生成器（Movie Commentary Builder）**：从分镜 JSON 生成 60 秒解说视频：
  ```bash
  python <SKILL_ROOT>/scripts/movie_commentary_builder.py --video "video.mp4" --json "storyboard.json"
  ```
- **同步本地素材（Sync Native Assets）**：从剪映 App 导入你收藏/使用过的 BGM/风格到技能：
  ```bash
  python <SKILL_ROOT>/scripts/sync_jy_assets.py
  # 从你已有的草稿中索引云端素材
  python <SKILL_ROOT>/scripts/build_cloud_music_library.py
  python <SKILL_ROOT>/scripts/build_cloud_text_styles_library.py
  ```
- **README 转教程（README to Tutorial）**：把一个项目的 README.md 转换成完整的安装教程视频脚本：
  - 阅读提示词：`prompts/readme_to_tutorial.md`
  - 把内容注入 `{{README_CONTENT}}` 变量
- **录屏与智能变焦（Screen Recorder & Smart Zoom）**：录制屏幕并自动应用变焦关键帧：
  ```bash
  python <SKILL_ROOT>/tools/recording/recorder.py
  # Web 预览捕获（高性能）
  python <SKILL_ROOT>/scripts/web_recorder.py --url "http://localhost:3000" --duration 5
  # 或对已有视频应用变焦：
  python <SKILL_ROOT>/scripts/jy_wrapper.py apply-zoom --name "Project" --video "v.mp4" --json "e.json"
  ```
- **草稿检查（Draft Inspector）**：检查草稿结构与元数据（v5.9+ 支持）：
  ```bash
  python <SKILL_ROOT>/scripts/draft_inspector.py list --limit 20
  python <SKILL_ROOT>/scripts/draft_inspector.py summary --name "DraftName"
  ```
- **自动导出（Auto Exporter）**：把草稿无头导出为 MP4/SRT：
  ```bash
  python <SKILL_ROOT>/scripts/auto_exporter.py "DraftName" "output.mp4" --res 1080 --fps 60
  # 仅导出 SRT：
  python <SKILL_ROOT>/scripts/jy_wrapper.py export-srt --name "DraftName"
  ```
  注意：MP4 自动导出使用 Windows UI 自动化。在 macOS 上，请生成草稿后在剪映中手动导出。
- **模板克隆与替换（Template Clone & Replacer）**：安全克隆模板并批量替换物料（防止损坏原模板）：
  ```bash
  # 克隆模板生成新项目
  python <SKILL_ROOT>/scripts/jy_wrapper.py clone --template "酒店模板" --name "客户A_副本"
  ```
- **API 校验（API Validator）**：对你的环境做一次快速诊断：
  ```bash
  python <SKILL_ROOT>/scripts/api_validator.py
  ```

## 🚀 快速开始示例

```python
import os
import sys

# 1. 环境初始化（必须同步到脚本开头，支持 Win/Mac）
current_dir = os.path.dirname(os.path.abspath(__file__))
env_root = os.getenv("JY_SKILL_ROOT", "").strip()
# 探测技能路径（支持 Antigravity、Trae、Claude 等）
skill_root = next((p for p in [
    env_root,
    os.path.join(current_dir, ".agents", "skills", "jianying-editor"),
    os.path.join(current_dir, ".agent", "skills", "jianying-editor"),
    os.path.join(current_dir, ".trae", "skills", "jianying-editor"),
    os.path.join(current_dir, ".claude", "skills", "jianying-editor"),
    os.path.join(current_dir, "skills", "jianying-editor"),
    os.path.abspath(".agents/skills/jianying-editor"),
    os.path.abspath(".agent/skills/jianying-editor"),
    os.path.abspath(".trae/skills/jianying-editor"),
    os.path.abspath(".claude/skills/jianying-editor"),
    os.path.abspath("skills/jianying-editor"),
    os.path.dirname(current_dir)
] if p and os.path.exists(os.path.join(p, "scripts", "jy_wrapper.py"))), None)

if not skill_root: raise ImportError("Could not find jianying-editor skill root.")
sys.path.insert(0, os.path.join(skill_root, "scripts"))
from jy_wrapper import JyProject

if __name__ == "__main__":
    # 2. 初始化工程（支持 v5.9+ 及自修复）
    project = JyProject("New AI Video", overwrite=True)
    assets_dir = os.path.join(skill_root, "assets")

    # 3. 智能配音与字幕（一键脚本转视频）
    project.add_narrated_subtitles(
        text="欢迎使用剪映自动化 Skill。这是一个全面适配 MacOS 的进阶版本。",
        speaker="zh_female_xiaopengyou"
    )

    # 4. 导入额外素材
    project.add_media_safe(os.path.join(assets_dir, "video.mp4"), "0s")
    project.add_media_safe(os.path.join(assets_dir, "audio.mp3"), "0s", track_name="Audio")

    # 5. 添加带动画的标题
    project.add_text_simple("剪映自动化开启", start_time="1s", duration="3s", anim_in="复古打字机")

    project.save()
```

## 🛠️ 初始化与项目规范（Initialization & Project Rules）

在初始化 `JyProject` 时，请务必根据主视频素材的比例设置分辨率。**默认值为横屏（1920x1080）**。

### 🚨 脚本存放位置规范

**禁止在技能安装目录下创建你的业务剪辑脚本**。

- **正确做法**：将你的剪辑 Python 脚本放在项目的根目录。
- **原因**：技能目录应该只包含工具集源码，便于后续 `git pull` 升级。业务代码混入会导致版本管理混乱。
