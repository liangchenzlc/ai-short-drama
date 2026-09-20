import type {
  AssetItem,
  MediaRef,
} from "../../../features/projects/episode-workflow";
import {
  hasAvailableMedia,
  mediaUrl,
  type ListedMedia,
} from "../../../features/projects/episode-media";

/** Small deterministic drawings: illustrative references, never generated media. */
export function ImagePreview({
  media,
  label,
  projectId,
  mediaItems = [],
  kind = "scene",
}: {
  media: MediaRef | null | undefined;
  label: string;
  projectId?: string;
  mediaItems?: readonly ListedMedia[];
  kind?: AssetItem["kind"];
}) {
  if (!media) return <span className="episode-image-empty">尚未采用图片</span>;
  if (media.kind === "project-image") {
    const url =
      projectId && hasAvailableMedia(mediaItems, media.id, "image/")
        ? mediaUrl(projectId, media.id)
        : null;
    return url ? (
      <img className="episode-illustration" src={url} alt={label} />
    ) : (
      <span className="episode-image-empty">
        项目图片不可用，需重新定位/替换
      </span>
    );
  }
  if (media.kind !== "demo-image") return <span>不可用于图片预览</span>;
  let seed = 0;
  for (const char of media.id)
    seed = (Math.imul(seed, 31) + char.charCodeAt(0)) >>> 0;
  const hue = seed % 360;
  const x = 90 + (seed % 130);
  const skyline = Array.from(
    { length: 7 },
    (_, index) => 25 + ((seed >>> (index * 3)) % 65),
  );
  return (
    <span className="episode-illustration-wrap">
      <svg
        className="episode-illustration"
        viewBox="0 0 320 180"
        role="img"
        aria-label={`${label} · 演示参考`}
      >
        <rect width="320" height="180" fill={`hsl(${hue} 36% 88%)`} />
        <circle
          cx={240 - (seed % 100)}
          cy={34 + (seed % 20)}
          r="22"
          fill={`hsl(${hue + 45} 70% 70%)`}
        />
        {skyline.map((height, index) => (
          <g key={index}>
            <rect
              x={index * 49}
              y={125 - height}
              width="39"
              height={height}
              fill={`hsl(${hue} 24% ${52 + index * 3}%)`}
            />
            <path
              d={`M${index * 49 + 8} ${135 - height}v${height - 18}m15 0v-${height - 18}`}
              stroke="white"
              strokeOpacity=".3"
              strokeWidth="4"
            />
          </g>
        ))}
        <path d="M0 132 L320 115 V180 H0Z" fill={`hsl(${hue} 22% 30%)`} />
        <path
          d="M0 175 L320 140 M110 180 L210 121"
          stroke="white"
          opacity=".25"
        />
        {kind === "prop" ? (
          <g transform={`translate(${x}, 63)`}>
            <path
              d="M-25 35 H25 L32 84 H-32Z"
              fill={`hsl(${hue + 160} 65% 66%)`}
            />
            <path
              d="M-15 35 V15 Q0 -4 15 15 V35"
              fill="none"
              stroke="#25344b"
              strokeWidth="7"
            />
            <circle cy="55" r="10" fill="#fff5cf" />
          </g>
        ) : (
          <g transform={`translate(${x}, ${kind === "character" ? 45 : 66})`}>
            <ellipse cy="100" rx="30" ry="6" fill="#142332" opacity=".25" />
            <circle cy="13" r="14" fill="#f0c9a8" />
            <path d="M-15 7 Q0 -16 15 7 V17 H-15Z" fill="#25344b" />
            <path
              d="M-17 34 Q0 25 17 34 L25 72 H-25Z"
              fill={`hsl(${hue + 160} 62% 58%)`}
            />
            <path
              d="M-10 72 L-14 98 M10 72 L18 98 M-17 40 L-33 60 M17 40 L33 50"
              stroke="#25344b"
              strokeWidth="8"
              strokeLinecap="round"
            />
          </g>
        )}
        <text x="12" y="19" fill="#25344b" fontSize="11">
          演示参考 · {seed.toString(36).slice(-4)}
        </text>
      </svg>
      <small>演示示意 · 非 AI 生成</small>
    </span>
  );
}
