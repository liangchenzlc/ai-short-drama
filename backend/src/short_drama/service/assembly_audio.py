"""多轨声音使用同一毫秒时钟；逐段处理限制同时解码器与命令行长度。"""

import json
import math
from array import array

from .render_tools import executable


def audible_clips(document, *, voices_only=False):
    muted = {t["id"] for t in document.get("tracks", []) if t["muted"]}
    return [
        c
        for c in document.get("audio_clips", [])
        if not c["muted"]
        and c["kind"] not in muted
        and (not voices_only or c["kind"] in {"original", "dialogue"})
    ]


def render_audio(renderer, clips, sources, directory, duration_ms, document=None, prefix="mix"):
    document = document or {}
    seconds = duration_ms / 1000
    binary = executable(renderer.settings.render_ffmpeg_path)
    paths = []
    voice_ranges = [
        *document.get("native_ducking", []),
        *[
            {"start_ms": c["start_ms"], "end_ms": c["start_ms"] + c["duration_ms"]}
            for c in audible_clips({**document, "audio_clips": clips})
            if c["kind"] == "dialogue"
        ],
    ]
    for index, clip in enumerate(clips):
        length = min(clip["duration_ms"], duration_ms - clip["start_ms"]) / 1000
        if length <= 0:
            continue
        source_length = (clip["trim_out_ms"] - clip["trim_in_ms"]) / 1000
        gain = clip["volume"] * 10 ** (clip.get("gain_db", 0) / 20)
        gain *= (
            document.get("original_volume", 1)
            if clip["kind"] == "original"
            else document.get("dialogue_volume", 1)
            if clip["kind"] == "dialogue"
            else 1
        )
        filters = (
            "aresample=48000,aformat=channel_layouts=stereo,"
            f"atrim=start={clip['trim_in_ms'] / 1000}:end={clip['trim_out_ms'] / 1000},"
            "asetpts=PTS-STARTPTS"
        )
        if clip["loop"]:
            filters += f",aloop=loop=-1:size={round(source_length * 48000)}"
            phase = clip.get("loop_offset_ms", 0) % (clip["trim_out_ms"] - clip["trim_in_ms"])
            filters += f",atrim=start={phase / 1000},asetpts=PTS-STARTPTS"
        filters += f",atrim=duration={length},volume={gain}"
        if clip["fade_in_ms"]:
            filters += f",afade=t=in:d={min(length, clip['fade_in_ms'] / 1000)}"
        if clip["fade_out_ms"]:
            fade = min(length, clip["fade_out_ms"] / 1000)
            filters += f",afade=t=out:st={max(0, length - fade)}:d={fade}"
        filters += f",adelay={clip['start_ms']}:all=1,apad,atrim=duration={seconds}"
        if clip["kind"] == "music" and clip["ducking"] and voice_ranges:
            expression = "+".join(
                f"between(t,{r['start_ms'] / 1000},{r['end_ms'] / 1000})" for r in voice_ranges
            )
            filters += f",volume='if(gt({expression},0),0.25,1)':eval=frame"
        path = directory / f"{prefix}-clip-{index}.wav"
        renderer.run(
            [
                binary,
                "-v",
                "error",
                "-nostdin",
                "-y",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(sources[clip["media_id"]]),
                "-vn",
                "-af",
                filters,
                "-t",
                str(seconds),
                "-c:a",
                "pcm_f32le",
                "-rf64",
                "auto",
                str(path),
            ],
            directory,
            stage="mixing",
            progress=90,
        )
        paths.append(path)
    if not paths:
        path = directory / f"{prefix}-silence.wav"
        renderer.run(
            [
                binary,
                "-v",
                "error",
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo",
                "-t",
                str(seconds),
                "-c:a",
                "pcm_f32le",
                str(path),
            ],
            directory,
            stage="mixing",
            progress=90,
        )
        paths = [path]
    generation = 0
    while len(paths) > 1:
        combined = []
        for offset in range(0, len(paths), 16):
            group = paths[offset : offset + 16]
            output = directory / f"{prefix}-group-{generation}-{offset}.wav"
            args = [binary, "-v", "error", "-nostdin", "-y"]
            for path in group:
                args += ["-i", str(path)]
            graph = (
                "".join(f"[{i}:a]" for i in range(len(group)))
                + f"amix=inputs={len(group)}:normalize=0:duration=longest[out]"
            )
            args += [
                "-filter_complex",
                graph,
                "-map",
                "[out]",
                "-t",
                str(seconds),
                "-c:a",
                "pcm_f32le",
                "-rf64",
                "auto",
                str(output),
            ]
            renderer.run(args, directory, stage="mixing", progress=91)
            combined.append(output)
            for path in group:
                path.unlink()
        paths = combined
        generation += 1
    return paths[0]


def analyse_audio(renderer, source, directory, start_ms=0, end_ms=None):
    binary = executable(renderer.settings.render_ffmpeg_path)
    args = [
        binary,
        "-v",
        "info",
        "-nostdin",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(source),
        "-ss",
        str(start_ms / 1000),
    ]
    if end_ms is not None:
        args += ["-t", str((end_ms - start_ms) / 1000)]
    renderer.run(
        [*args, "-vn", "-af", "loudnorm=I=-16:TP=-1:LRA=11:print_format=json", "-f", "null", "-"],
        directory,
        stage="analysing",
        progress=40,
    )
    report = directory / "process.stderr"
    raw = report.read_text(encoding="utf-8", errors="replace")
    left, right = raw.rfind("{"), raw.rfind("}")
    stats = json.loads(raw[left : right + 1]) if left >= 0 else {}
    integrated, peak = float(stats.get("input_i", "-inf")), float(stats.get("input_tp", "-inf"))
    gain = (
        min(24, -16 - integrated, -1 - peak)
        if math.isfinite(integrated) and math.isfinite(peak)
        else 0
    )
    waveform_file = directory / "waveform.f32"
    renderer.run(
        [
            *args[:2],
            "error",
            *args[3:],
            "-vn",
            "-ac",
            "1",
            "-ar",
            "2000",
            "-f",
            "f32le",
            "-y",
            str(waveform_file),
        ],
        directory,
        stage="analysing",
        progress=70,
    )
    samples = array("f")
    samples.frombytes(waveform_file.read_bytes())
    stride = max(1, math.ceil(len(samples) / 1024))
    waveform = [
        round(min(1, max(abs(v) for v in samples[i : i + stride])), 4)
        for i in range(0, len(samples), stride)
    ]
    return {
        "waveform": waveform,
        "integrated_lufs": integrated if math.isfinite(integrated) else None,
        "peak_db": peak if math.isfinite(peak) else None,
        "gain_db": round(max(-60, gain), 3),
    }
