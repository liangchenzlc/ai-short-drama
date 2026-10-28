from short_drama.ai.gateway.endpoints import endpoint as endpoint


def video_input_capabilities(snapshot, adapter):
    model = snapshot.get("model_key", "")
    if adapter == "modelhub_video.v1":
        if model == "minimax-h3":
            return {
                "first_frame": True,
                "reference_images": True,
                "duration_seconds": list(range(5, 16)),
                "resolutions": ["480p"],
                "audio_references": False,
                "generate_audio": False,
                "max_audio_references": 0,
                "audio_min_ms": 3000,
                "audio_max_ms": 7500,
                "audio_evidence": "unsupported",
                "voice_fidelity": "unverified",
            }
        supported = model == "seedance-2.0-mini"
        return {
            "first_frame": supported,
            "reference_images": supported,
            "duration_seconds": list(range(4, 16)) if supported else None,
            "resolutions": ["720p", "480p"] if supported else None,
            "audio_references": supported,
            "generate_audio": supported,
            "max_audio_references": 2 if supported else 0,
            "audio_min_ms": 3000,
            "audio_max_ms": 7500,
            "audio_evidence": "documented_channel_dependent" if supported else "unsupported",
            "voice_fidelity": "unverified",
        }
    durations = {
        "wan2.2-i2v-plus": [5],
        "wan2.2-i2v-flash": [5],
        "wanx2.1-i2v-plus": [5],
        "wanx2.1-i2v-turbo": [5],
        "wan2.6-i2v": [5, 10, 15],
        "wan2.5-i2v-preview": [5, 10],
    }.get(model)
    resolutions = {
        "wan2.2-i2v-plus": ["480p", "1080p"],
        "wanx2.1-i2v-plus": ["720p"],
        "wanx2.1-i2v-turbo": ["480p", "720p"],
        "wan2.6-i2v": ["720p", "1080p"],
    }.get(model)
    return {
        "reference_images": False,
        "first_frame": adapter == "ark_video.v1"
        or (adapter == "dashscope_video.v1" and "i2v" in model),
        "duration_seconds": durations,
        "resolutions": resolutions,
    }
