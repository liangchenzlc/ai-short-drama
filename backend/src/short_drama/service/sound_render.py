"""Mix on a fixed 48 kHz clock without extending or retiming the video."""

import shutil
from functools import lru_cache
from pathlib import Path

from .render_tools import checksum, executable


@lru_cache(maxsize=8)
def _font_checksum(path, size, modified):
    return checksum(path)


def font_metadata(settings):
    path = Path(settings.render_subtitle_font_path)
    family = settings.render_subtitle_font_family
    if not settings.render_subtitle_font_path:
        for candidate, name in (
            ("C:/Windows/Fonts/msyh.ttc", "Microsoft YaHei"),
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "Noto Sans CJK SC"),
        ):
            if Path(candidate).is_file():
                path, family = Path(candidate), name
                break
    result = {
        "font": family,
        "font_file": str(path.resolve()),
        "font_checksum": None,
    }
    if path.is_file():
        stat = path.stat()
        result["font_checksum"] = _font_checksum(
            str(path.resolve()), stat.st_size, stat.st_mtime_ns
        )
    return result


def ass_text(value):
    # Prevent user text becoming ASS overrides or escapes. Zero-width separators
    # preserve visible punctuation while preventing libass command recognition.
    return (
        value.replace("\\", "\\\u200b").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")
    )


def write_ass(path, subtitles, size, family, width, height, settings=None):
    def stamp(ms):
        centis = ms // 10
        seconds, centis = divmod(centis, 100)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02}:{seconds:02}.{centis:02}"

    style_format = (
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
    )
    style = (
        f"Style: Default,{family},{size * height / 720},&H00FFFFFF,&H00FFFFFF,"
        "&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,24,24,30,1"
    )
    if settings:

        def color(value, opacity=1):
            return f"&H{round((1 - opacity) * 255):02X}{value[5:7]}{value[3:5]}{value[1:3]}"

        margin = round(min(width, height) * settings["margin_percent"] / 100)
        style = (
            f"Style: Default,{family},{size * height / 720},{color(settings['color'])},&H00FFFFFF,"
            f"{color(settings['outline_color'])},&H80000000,{-1 if settings['bold'] else 0},"
            "0,0,0,100,100,0,0,1,"
            f"{settings['outline_width'] * height / 720},0,{settings['alignment']},"
            f"{margin},{margin},{margin},1"
        )
        if settings["background"]:
            style += (
                f"\nStyle: Box,{family},{size * height / 720},&HFFFFFFFF,&HFFFFFFFF,"
                f"{color(settings['background_color'], settings['background_opacity'])},&HFF000000,"
                f"{-1 if settings['bold'] else 0},0,0,0,100,100,0,0,3,5,0,"
                f"{settings['alignment']},{margin},{margin},{margin},1"
            )
    text = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
[V4+ Styles]
{style_format}
{style}
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    for entry in subtitles:
        if settings and settings["background"]:
            text += (
                f"Dialogue: 0,{stamp(entry['start_ms'])},{stamp(entry['end_ms'])},"
                f"Box,,0,0,0,,{ass_text(entry['text'])}\n"
            )
        text += (
            f"Dialogue: 1,{stamp(entry['start_ms'])},{stamp(entry['end_ms'])},"
            f"Default,,0,0,0,,{ass_text(entry['text'])}\n"
        )
    path.write_text(text, encoding="utf-8")


def voice_filter(index, line, volume, seconds, tag):
    return (
        f"[{index}:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,"
        f"volume={volume},adelay={line['start_ms']}:all=1,apad,atrim=duration={seconds}[{tag}]"
    )


def group_dialogue(renderer, lines, volume, seconds, sources, directory):
    """Bound Windows argv length and simultaneously opened decoders for long scripts."""
    grouped, paths = [], dict(sources)
    for offset in range(0, len(lines), 16):
        group = lines[offset : offset + 16]
        args = [executable(renderer.settings.render_ffmpeg_path), "-v", "error", "-nostdin", "-y"]
        graph = []
        for index, line in enumerate(group):
            args += ["-protocol_whitelist", "file,pipe", "-i", str(sources[line["media_id"]])]
            graph.append(voice_filter(index, line, volume, seconds, f"v{index}"))
        graph.append(
            "".join(f"[v{i}]" for i in range(len(group)))
            + f"amix=inputs={len(group)}:normalize=0:duration=longest[mix]"
        )
        script = directory / f"voice-group-{offset}.txt"
        script.write_text(";".join(graph), encoding="utf-8")
        output = directory / f"voice-group-{offset}.wav"
        args += [
            "-filter_complex_script",
            str(script),
            "-map",
            "[mix]",
            "-t",
            str(seconds),
            "-c:a",
            "pcm_f32le",
            "-rf64",
            "auto",
            str(output),
        ]
        renderer.run(args, directory, stage="mixing", progress=89)
        key = f"voice-group-{offset}"
        paths[key] = output
        grouped.append({"media_id": key, "start_ms": 0})
    return grouped, paths


def mix_sound(renderer, source, sound, sources, directory, total_ms, width, height):
    doc = sound["document"]
    seconds = total_ms / 1000
    multi = doc.get("schema_version", 1) >= 2
    dialogue = [] if multi else doc["dialogue"]
    dialogue_volume = doc["dialogue_volume"]
    if len(dialogue) > 16:
        dialogue, sources = group_dialogue(
            renderer, dialogue, dialogue_volume, seconds, sources, directory
        )
        dialogue_volume = 1
    args = [
        executable(renderer.settings.render_ffmpeg_path),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(source),
    ]
    graph = [
        "[0:a]aresample=48000,aformat=channel_layouts=stereo,"
        f"volume={doc['original_volume']},apad,atrim=duration={seconds}[original]"
    ]
    inputs = 1
    voices = []
    for line in dialogue:
        args += ["-protocol_whitelist", "file,pipe", "-i", str(sources[line["media_id"]])]
        tag = f"voice{inputs}"
        graph.append(voice_filter(inputs, line, dialogue_volume, seconds, tag))
        voices.append(f"[{tag}]")
        inputs += 1
    channels = ["[original]"]
    music = None if multi else doc["music"]
    duck = bool(music and music["ducking"] and voices)
    if voices:
        graph.append(
            "".join(voices) + f"amix=inputs={len(voices)}:normalize=0:duration=longest[dialogue]"
        )
        if duck:
            # 对齐压缩器两路输入的样本时钟和帧大小，保留延后开始的配乐尾段。
            graph.append(
                "[dialogue]asplit=2[spoken][sidechain_raw];"
                "[sidechain_raw]asetpts=N/SR/TB,asetnsamples=n=1024:p=1[sidechain]"
            )
        channels.append("[spoken]" if duck else "[dialogue]")
    if music:
        music_source = sources[music["media_id"]]
        start, end = music["trim_in_ms"] / 1000, music["trim_out_ms"] / 1000
        available = max(0, seconds - music["start_ms"] / 1000)
        length = available if music["loop"] else min(available, end - start)
        fade_in = min(length, music["fade_in_ms"] / 1000)
        fade_out = min(length, music["fade_out_ms"] / 1000)
        filters = (
            f"[{inputs}:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"atrim=start={start}:end={end},asetpts=PTS-STARTPTS"
        )
        if music["loop"]:
            filters += f",aloop=loop=-1:size={round((end - start) * 48000)}"
        filters += (
            f",atrim=duration={length},volume={music['volume']},afade=t=in:d={fade_in},"
            f"afade=t=out:st={max(0, length - fade_out)}:d={fade_out},"
            f"adelay={music['start_ms']}:all=1,apad,atrim=duration={seconds},asetpts=N/SR/TB[music]"
        )
        # 先冻结配乐的延迟、裁剪和淡入淡出，避免 sidechain 调度丢失静音样本。
        music_script = directory / "music-track.txt"
        music_script.write_text(filters.replace(f"[{inputs}:a]", "[0:a]"), encoding="utf-8")
        music_output = directory / "music-track.wav"
        renderer.run(
            [
                executable(renderer.settings.render_ffmpeg_path),
                "-v",
                "error",
                "-nostdin",
                "-y",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(music_source),
                "-filter_complex_threads",
                "1",
                "-filter_complex_script",
                str(music_script),
                "-map",
                "[music]",
                "-t",
                str(seconds),
                "-c:a",
                "pcm_f32le",
                "-rf64",
                "auto",
                str(music_output),
            ],
            directory,
            stage="mixing",
            progress=90,
        )
        music_source = music_output
        filters = f"[{inputs}:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=N/SR/TB"
        if duck:
            filters += ",asetnsamples=n=1024:p=1"
        filters += "[music]"
        args += ["-protocol_whitelist", "file,pipe", "-i", str(music_source)]
        graph.append(filters)
        intervals = doc.get("native_ducking", []) if sound.get("mode") == "native" else []
        if music["ducking"] and intervals:
            expression = "+".join(
                f"between(t,{s['start_ms'] / 1000},{s['end_ms'] / 1000})" for s in intervals
            )
            graph.append(f"[music]volume='if(gt({expression},0),0.25,1)':eval=frame[native_music]")
        if duck:
            graph.append(
                "[music][sidechain]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=250[ducked]"
            )
        channels.append(
            "[ducked]"
            if duck
            else "[native_music]"
            if music["ducking"] and intervals
            else "[music]"
        )
    graph.append(
        "".join(channels)
        + f"amix=inputs={len(channels)}:normalize=0:duration=longest,"
        + f"alimiter=limit=0.95:level=0:latency=1,atrim=duration={seconds}[mix]"
    )
    script = directory / "sound-mix.txt"
    script.write_text(";".join(graph), encoding="utf-8")
    soundtrack = directory / "soundtrack.wav"
    args += [
        "-filter_complex_threads",
        "1",
        "-filter_complex_script",
        str(script),
        "-map",
        "[mix]",
        "-t",
        str(seconds),
        "-c:a",
        "pcm_f32le",
        "-rf64",
        "auto",
        str(soundtrack),
    ]
    if multi:
        from .assembly_audio import audible_clips, render_audio

        soundtrack = render_audio(renderer, audible_clips(doc), sources, directory, total_ms, doc)
    else:
        renderer.run(args, directory, stage="mixing", progress=91)
    # 音轨已完整输出后再编码字幕画面，避免音频分支与视频编码相互等待。
    args = [
        executable(renderer.settings.render_ffmpeg_path),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(source),
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(soundtrack),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-t",
        str(seconds),
    ]
    subtitles_enabled = not any(
        t["id"] == "subtitles" and t["muted"] for t in doc.get("tracks", [])
    )
    if doc["burn_subtitles"] and doc["subtitles"] and subtitles_enabled:
        font = Path(sound["font_file"])
        if not font.is_file() or checksum(font) != sound["font_checksum"]:
            raise RuntimeError("字幕字体已变化，无法复现本次导出，请重新提交")
        fonts = directory / "fonts"
        fonts.mkdir(exist_ok=True)
        shutil.copyfile(font, fonts / ("subtitle" + font.suffix))
        subtitle = directory / "subtitles.ass"
        write_ass(
            subtitle,
            doc["subtitles"],
            doc["font_size"],
            sound["font"],
            width,
            height,
            doc.get("subtitle_style") if multi else None,
        )

        def escaped(path):
            return str(path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")

        args += [
            "-vf",
            f"ass=filename='{escaped(subtitle)}':fontsdir='{escaped(fonts)}'",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "2",
        ]
    else:
        args += ["-c:v", "copy"]
    output = directory / "mixed.part.mp4"
    args += [
        "-af",
        "alimiter=limit=0.891251:level=0:latency=1" if multi else "anull",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output),
    ]
    renderer.run(args, directory, stage="mixing", progress=92)
    output.replace(source)
