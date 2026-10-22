"""标准成片媒体任务执行：真实音频转写与波形/响度分析。"""

from uuid import uuid4

from .assembly_audio import analyse_audio, audible_clips, render_audio
from .canvas_cloud_transcription import (
    CanvasCloudTranscriptionExecutor,
    require_cloud_transcription,
)
from .canvas_transcription_execute import CanvasTranscriptionExecutor
from .video_render import executable


def execute_media_job(
    renderer, snapshot, kind, sources, directory, config=None, before_submit=lambda: None
):
    if kind == "audio_analysis":
        value = snapshot["analysis"]
        return {
            **value,
            **analyse_audio(
                renderer,
                sources[value["media_id"]],
                directory,
                value["trim_in_ms"],
                value["trim_out_ms"],
            ),
        }
    request = snapshot["transcription"]
    document = snapshot.get("sound", {}).get("document", {})
    if document.get("schema_version", 1) >= 2:
        clips = audible_clips(document, voices_only=True)
    else:
        clips = []
        for clip, span in zip(snapshot["clips"], request["ranges"], strict=True):
            if clip["muted"] or not clip.get("has_audio"):
                continue
            clips.append(
                {
                    "kind": "original",
                    "media_id": clip["media_id"],
                    "start_ms": span["start_ms"],
                    "trim_in_ms": clip["trim_in_ms"],
                    "trim_out_ms": clip["trim_out_ms"],
                    "duration_ms": span["end_ms"] - span["start_ms"],
                    "volume": 1,
                    "fade_in_ms": 0,
                    "fade_out_ms": 0,
                    "loop": False,
                    "ducking": False,
                }
            )
        for line in document.get("dialogue", []):
            media = next(
                (
                    m
                    for m in snapshot.get("sound", {}).get("media", [])
                    if m["media_id"] == line["media_id"]
                ),
                None,
            )
            if media:
                clips.append(
                    {
                        "kind": "dialogue",
                        "media_id": line["media_id"],
                        "start_ms": line["start_ms"],
                        "trim_in_ms": 0,
                        "trim_out_ms": media["duration_ms"],
                        "duration_ms": media["duration_ms"],
                        "volume": 1,
                        "fade_in_ms": 0,
                        "fade_out_ms": 0,
                        "loop": False,
                        "ducking": False,
                    }
                )
    if not clips:
        raise RuntimeError("识别范围没有有效人声音轨，请检查静音和声音来源")
    total = request["ranges"][-1]["end_ms"]
    soundtrack = render_audio(renderer, clips, sources, directory, total, document, prefix="voices")
    wav = directory / "recognition.wav"
    renderer.run(
        [
            executable(renderer.settings.render_ffmpeg_path),
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(soundtrack),
            "-ss",
            str(request["start_ms"] / 1000),
            "-t",
            str((request["end_ms"] - request["start_ms"]) / 1000),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(wav),
        ],
        directory,
        stage="transcribing",
        progress=30,
    )
    if config is not None:
        require_cloud_transcription(config, request["model_version"])
        executor = CanvasCloudTranscriptionExecutor(
            renderer.settings, config, renderer.heartbeat, before_submit=before_submit
        )
    else:
        executor = CanvasTranscriptionExecutor(renderer.settings, renderer.heartbeat)
    result = executor.transcribe(wav, request["language"])
    subtitles = []
    previous_end = request["start_ms"]
    for segment in sorted(result.segments, key=lambda item: item.start_ms):
        start = max(previous_end, request["start_ms"] + max(0, segment.start_ms))
        end = min(request["end_ms"], request["start_ms"] + segment.end_ms)
        if end <= start or not segment.text.strip():
            continue
        text = segment.text.strip()
        cut = 0
        for span in request["ranges"]:
            a, b = max(start, span["start_ms"]), min(end, span["end_ms"])
            if b > a:
                boundary = len(text) if b == end else round(len(text) * (b - start) / (end - start))
                part = text[cut:boundary].strip()
                cut = boundary
                if not part:
                    continue
                subtitles.append(
                    {
                        "id": str(uuid4()),
                        "start_ms": a,
                        "end_ms": b,
                        "text": part,
                        "anchor_clip_id": span["clip_id"],
                        "anchor_source_in_ms": span["source_in_ms"] + a - span["start_ms"],
                    }
                )
        previous_end = end
    if not subtitles:
        raise RuntimeError("未识别出有效字幕，当前字幕保持不变")
    return {
        "subtitles": subtitles,
        "language": result.language,
        "start_ms": request["start_ms"],
        "end_ms": request["end_ms"],
        "source_hash": request["source_hash"],
        "voice_context_hash": request["voice_context_hash"],
        "ranges": request["ranges"],
    }
