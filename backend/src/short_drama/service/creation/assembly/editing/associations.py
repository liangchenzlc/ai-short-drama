"""同步分镜时按来源时间重建关联，固定整集位置的声音不移动。"""

from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5


def picture_spans(video):
    result, cursor = {}, 0
    for clip in video["clips"]:
        if clip["trim_out_ms"] is None:
            continue
        frames = (clip["trim_out_ms"] * 30 + 500) // 1000 - (clip["trim_in_ms"] * 30 + 500) // 1000
        end = cursor + max(0, frames)
        result[clip["clip_id"]] = (clip, round(cursor * 1000 / 30), round(end * 1000 / 30))
        cursor = end
    return result


def sync_associations(document, before, after):
    doc = deepcopy(document)
    old, new = picture_spans(before), picture_spans(after)
    for field in ("audio_clips", "subtitles"):
        rebuilt = []
        for item in doc.get(field, []):
            anchor = item.get("anchor_clip_id")
            if not anchor:
                rebuilt.append(item)
                continue
            if anchor not in new:
                # A pending probe has no reliable duration. Preserve decisions until it completes.
                if any(c["clip_id"] == anchor and c["trim_out_ms"] is None for c in after["clips"]):
                    rebuilt.append(item)
                continue
            clip, start, end = new[anchor]
            source_start = item.get("anchor_source_in_ms")
            if source_start is None:
                previous = old.get(anchor)
                source_start = (
                    (previous[0]["trim_in_ms"] + item["start_ms"] - previous[1])
                    if previous
                    else clip["trim_in_ms"]
                )
            length = (
                item["duration_ms"] if field == "audio_clips" else item["end_ms"] - item["start_ms"]
            )
            a, b = (
                max(source_start, clip["trim_in_ms"]),
                min(source_start + length, clip["trim_in_ms"] + end - start),
            )
            if item.get("kind") == "original":
                if clip.get("source_type") == "image" or not clip.get("has_audio"):
                    continue
                item["media_id"] = clip["media_id"]
                previous = old.get(anchor)
                if (
                    previous
                    and abs(item["trim_in_ms"] - previous[0]["trim_in_ms"]) <= 1
                    and abs(
                        item["duration_ms"]
                        - min(
                            previous[2] - previous[1],
                            previous[0]["duration_ms"] - previous[0]["trim_in_ms"],
                        )
                    )
                    <= 1
                ):
                    a, b = clip["trim_in_ms"], clip["trim_in_ms"] + end - start
                b = min(b, clip["duration_ms"])
            if b <= a:
                continue
            cut = a - source_start
            item.update(start_ms=start + a - clip["trim_in_ms"], anchor_source_in_ms=a)
            if field == "audio_clips":
                item["duration_ms"] = b - a
                if item["kind"] == "original":
                    item.update(trim_in_ms=a, trim_out_ms=b)
                    if previous and previous[0]["muted"] != clip["muted"]:
                        item["muted"] = clip["muted"]
                elif not item["loop"]:
                    item["trim_in_ms"] += cut
                    item["trim_out_ms"] = item["trim_in_ms"] + b - a
                else:
                    item["loop_offset_ms"] = (item.get("loop_offset_ms", 0) + cut) % (
                        item["trim_out_ms"] - item["trim_in_ms"]
                    )
            else:
                item["end_ms"] = item["start_ms"] + b - a
            rebuilt.append(item)
        doc[field] = sorted(rebuilt, key=lambda c: c["start_ms"])
    for anchor, (clip, start, end) in new.items():
        if anchor in old or not clip.get("has_audio") or clip.get("source_type") == "image":
            continue
        if any(
            c.get("anchor_clip_id") == anchor and c["kind"] == "original"
            for c in doc.get("audio_clips", [])
        ):
            continue
        duration = min(end - start, clip["duration_ms"] - clip["trim_in_ms"])
        if duration <= 0:
            continue
        doc.setdefault("audio_clips", []).append(
            {
                "id": str(uuid5(NAMESPACE_URL, f"assembly:original:{anchor}")),
                "kind": "original",
                "name": "原声",
                "media_id": clip["media_id"],
                "start_ms": start,
                "duration_ms": duration,
                "trim_in_ms": clip["trim_in_ms"],
                "trim_out_ms": clip["trim_in_ms"] + duration,
                "volume": 1,
                "gain_db": 0,
                "fade_in_ms": 0,
                "fade_out_ms": 0,
                "loop": False,
                "ducking": False,
                "muted": clip["muted"],
                "anchor_clip_id": anchor,
                "anchor_source_in_ms": clip["trim_in_ms"],
            }
        )
    return doc
