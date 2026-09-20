import React, { useEffect, useRef, useState } from "react";
import {
  DEMO_MODELS,
  sampleMotion,
} from "../../../features/projects/episode-demo";
import {
  importProjectMedia,
  hasAvailableMedia,
  listUsableMedia,
  mediaUrl,
  type ListedMedia,
} from "../../../features/projects/episode-media";
import {
  frameRoleReview,
  isScriptCurrent,
  type DemoRun,
  type EpisodeWorkflow,
  type ShotItem,
} from "../../../features/projects/episode-workflow";
import {
  assetImageReady,
  shotInputIssue,
  storyboardReady,
} from "../../../features/projects/episode-review";
import { ImagePreview } from "./ImagePreview";

const missingFrame = "请先选定本镜可用的单幅分镜图";

export function canGenerateVideo(
  shot: ShotItem,
  mediaItems: readonly ListedMedia[] = [],
  blockedImageIds: readonly string[] = [],
): { ok: true } | { ok: false; reason: string } {
  if (shot.review !== "confirmed")
    return { ok: false, reason: "请先确认本镜分镜文字" };
  const selected = shot.firstFrames.find(
    (candidate) => candidate.id === shot.selectedFirstId,
  );
  if (
    frameRoleReview(shot, "first") !== "confirmed" ||
    !selected?.usableForVideo ||
    !["demo-image", "project-image"].includes(selected.value.kind) ||
    blockedImageIds.includes(selected.value.id)
  )
    return { ok: false, reason: missingFrame };
  if (selected.value.kind === "project-image") {
    const available = hasAvailableMedia(
      mediaItems,
      selected.value.id,
      "image/",
    );
    if (!available)
      return {
        ok: false,
        reason: "项目首帧图片不可用或格式不符，请刷新项目媒体并重选",
      };
  }
  return { ok: true };
}

function blockedFrames(value: EpisodeWorkflow) {
  return value.gridBatches.flatMap((batch) => [
    batch.sheet.id,
    ...batch.cells
      .filter(
        (cell) =>
          cell.ref &&
          (cell.review !== "confirmed" || cell.ref.kind === "project-image"),
      )
      .map((cell) => cell.ref!.id),
  ]);
}
export function videoInputGate(
  value: EpisodeWorkflow,
  shot: ShotItem,
  media: readonly ListedMedia[] = [],
) {
  const issue = shotInputIssue(value, shot, media);
  if (issue) return { ok: false as const, reason: issue };
  if (value.reviews.storyboard !== "confirmed")
    return { ok: false as const, reason: "请先返回分镜阶段确认当前分镜" };
  return canGenerateVideo(shot, media, blockedFrames(value));
}

export function generateDemoVideo(
  value: EpisodeWorkflow,
  shotId: string,
  media: readonly ListedMedia[] = [],
): EpisodeWorkflow {
  const shot = value.shots.find((item) => item.id === shotId);
  if (!shot || !videoInputGate(value, shot, media).ok) return value;
  const candidate = {
    id: crypto.randomUUID(),
    source: "demo" as const,
    value: sampleMotion(shotId),
  };
  return {
    ...value,
    shots: value.shots.map((item) =>
      item.id === shotId
        ? {
            ...item,
            videos: [...item.videos, candidate],
            selectedVideoId: candidate.id,
            videoReview: "review" as const,
          }
        : item,
    ),
    reviews: { ...value.reviews, video: "review" },
  };
}

export function failVideo(
  value: EpisodeWorkflow,
  shotId: string,
): EpisodeWorkflow {
  // A failed attempt only changes review state; all earlier candidates remain accessible.
  return {
    ...value,
    shots: value.shots.map((shot) =>
      shot.id === shotId
        ? {
            ...shot,
            videoReview: shot.videos.length
              ? shot.videoReview
              : ("review" as const),
          }
        : shot,
    ),
  };
}

export function confirmVideo(
  value: EpisodeWorkflow,
  shotId: string,
  candidateId: string,
  media: readonly ListedMedia[] = [],
): EpisodeWorkflow {
  const shot = value.shots.find((item) => item.id === shotId);
  const candidate = shot?.videos.find((item) => item.id === candidateId);
  if (
    !shot ||
    !candidate ||
    !videoInputGate(value, shot, media).ok ||
    !(
      candidate.value.kind === "demo-motion" ||
      (candidate.value.kind === "project-video" &&
        hasAvailableMedia(media, candidate.value.id, "video/mp4"))
    )
  )
    return value;
  const shots = value.shots.map((item) =>
    item.id === shotId
      ? {
          ...item,
          selectedVideoId: candidateId,
          videoReview: "confirmed" as const,
        }
      : item,
  );
  return {
    ...value,
    shots,
    reviews: {
      ...value.reviews,
      video: shots.every((item) => item.videoReview === "confirmed")
        ? "confirmed"
        : "review",
    },
  };
}

const reviewText = {
  not_started: "未开始",
  review: "待核对",
  confirmed: "已确认",
  stale: "需复核",
};

export function VideoStage({
  value,
  readOnly,
  onChange,
  projectId,
  ready = true,
  mediaItems: suppliedMedia,
  onApply,
}: {
  value: EpisodeWorkflow;
  readOnly: boolean;
  onChange: (next: EpisodeWorkflow) => void;
  onApply?: (change: (current: EpisodeWorkflow) => EpisodeWorkflow) => void;
  projectId?: string;
  ready?: boolean;
  mediaItems?: readonly ListedMedia[];
}) {
  const [listedItems, setMediaItems] = useState<readonly ListedMedia[]>(
    suppliedMedia ?? [],
  );
  const mediaItems = ready ? (suppliedMedia ?? listedItems) : [];
  const [mediaChecked, setMediaChecked] = useState(Boolean(suppliedMedia));
  const [runs, setRuns] = useState<Record<string, DemoRun>>({});
  const [messages, setMessages] = useState<Record<string, string>>({});
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const latestValue = useRef(value);
  latestValue.current = value;
  const lifetime = useRef({ active: true });
  useEffect(() => {
    const token = { active: true };
    lifetime.current = token;
    return () => {
      token.active = false;
      timers.current.forEach(clearTimeout);
    };
  }, [projectId, readOnly, ready]);
  function apply(change: (current: EpisodeWorkflow) => EpisodeWorkflow) {
    if (onApply) onApply(change);
    else {
      latestValue.current = change(latestValue.current);
      onChange(latestValue.current);
    }
  }
  useEffect(() => {
    let active = true;
    if (!projectId || !ready || suppliedMedia) return;
    setMediaChecked(false);
    void Promise.all([
      listUsableMedia(projectId, "image/"),
      listUsableMedia(projectId, "video/"),
    ]).then(([images, videos]) => {
      if (active) {
        setMediaItems([...images, ...videos]);
        setMediaChecked(true);
      }
    });
    return () => {
      active = false;
    };
  }, [projectId, ready, suppliedMedia]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  async function refreshMedia() {
    const token = lifetime.current;
    if (!projectId || !ready) return suppliedMedia ?? [];
    const [images, videos] = await Promise.all([
      listUsableMedia(projectId, "image/"),
      listUsableMedia(projectId, "video/"),
    ]);
    const fresh = [...images, ...videos];
    if (token.active) {
      setMediaItems(fresh);
      setMediaChecked(true);
    }
    return fresh;
  }

  async function generate(shot: ShotItem) {
    const token = lifetime.current;
    if (
      readOnly ||
      Object.values(runs).some(
        (run) => run === "preparing" || run === "running",
      )
    )
      return;
    setRuns((current) => ({ ...current, [shot.id]: "preparing" }));
    const fresh = await refreshMedia();
    if (!token.active) return;
    const currentShot = latestValue.current.shots.find(
      (item) => item.id === shot.id,
    );
    if (!currentShot) return;
    const gate = videoInputGate(latestValue.current, currentShot, fresh);
    if (!gate.ok) {
      setRuns((current) => ({ ...current, [shot.id]: "failed" }));
      setMessages((current) => ({ ...current, [shot.id]: gate.reason }));
      return;
    }
    setMessages((current) => ({ ...current, [shot.id]: "" }));
    setRuns((current) => ({ ...current, [shot.id]: "running" }));
    timers.current.push(
      setTimeout(() => {
        if (!token.active) return;
        apply((current) => generateDemoVideo(current, shot.id, fresh));
        setRuns((current) => ({ ...current, [shot.id]: "idle" }));
      }, 450),
    );
  }

  async function importVideo(shot: ShotItem) {
    if (readOnly || !projectId || !ready) return;
    const token = lifetime.current;
    const result = await importProjectMedia(projectId, "video");
    if (!token.active) return;
    if (!result.ok) {
      if (result.reason !== "cancelled")
        setMessages((current) => ({
          ...current,
          [shot.id]: result.message ?? "导入结果不确定，请刷新项目媒体后重试。",
        }));
      return;
    }
    const fresh = await refreshMedia();
    if (!token.active) return;
    if (!hasAvailableMedia(fresh, result.mediaId, "video/mp4")) {
      setMessages((current) => ({
        ...current,
        [shot.id]: "导入媒体并非可用 MP4，未加入候选。",
      }));
      return;
    }
    const candidate = {
      id: crypto.randomUUID(),
      source: "import" as const,
      value: { kind: "project-video" as const, id: result.mediaId },
    };
    apply((current) =>
      current.shots.some((item) => item.id === shot.id)
        ? {
            ...current,
            shots: current.shots.map((item) =>
              item.id === shot.id
                ? {
                    ...item,
                    videos: [...item.videos, candidate],
                    selectedVideoId: candidate.id,
                    videoReview: "review",
                  }
                : item,
            ),
            reviews: { ...current.reviews, video: "review" },
          }
        : current,
    );
    setMessages((current) => ({
      ...current,
      [shot.id]: "已导入项目 MP4，待核对。",
    }));
  }

  const completed =
    value.shots.length > 0 &&
    value.assets.length > 0 &&
    value.reviews.assets === "confirmed" &&
    value.assets.every((asset) => assetImageReady(asset, mediaItems)) &&
    value.reviews.storyboard === "confirmed" &&
    storyboardReady(value, mediaItems) &&
    value.shots.every(
      (shot) =>
        videoInputGate(value, shot, mediaItems).ok &&
        shot.videoReview === "confirmed" &&
        shot.videos.some(
          (candidate) =>
            candidate.id === shot.selectedVideoId &&
            (candidate.value.kind === "demo-motion" ||
              (candidate.value.kind === "project-video" &&
                hasAvailableMedia(
                  mediaItems,
                  candidate.value.id,
                  "video/mp4",
                ))),
        ),
    ) &&
    isScriptCurrent(value) &&
    value.assets.every((asset) => asset.review === "confirmed");
  return (
    <>
      <div className="episode-stage-heading">
        <div>
          <h2>分镜视频</h2>
          <p>
            逐镜核对首帧并体验动作预览。演示动效不是视频文件，不会调用 AI
            或产生费用。
          </p>
        </div>
        <span>
          {
            value.shots.filter((shot) => shot.videoReview === "confirmed")
              .length
          }{" "}
          / {value.shots.length} 已确认
        </span>
      </div>
      <div className="episode-stage-controls">
        <label>
          视频模型
          <select
            value={value.models.video}
            disabled={readOnly}
            onChange={(event) =>
              onChange({
                ...value,
                models: { ...value.models, video: event.target.value },
              })
            }
          >
            {!DEMO_MODELS.video.some(
              (model) => model.value === value.models.video,
            ) && (
              <option value={value.models.video}>{value.models.video}</option>
            )}
            {DEMO_MODELS.video.map((model) => (
              <option key={model.value} value={model.value}>
                {model.label}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={!projectId || !ready}
          onClick={() => void refreshMedia()}
        >
          刷新项目媒体
        </button>
      </div>
      {!value.shots.length && (
        <p className="episode-help">
          先在分镜阶段建立镜头，再逐镜生成演示动效。
        </p>
      )}
      {value.shots.map((shot, index) => {
        const frame = shot.firstFrames.find(
          (candidate) => candidate.id === shot.selectedFirstId,
        );
        const gate = videoInputGate(value, shot, mediaItems);
        const selected = shot.videos.find(
          (candidate) => candidate.id === shot.selectedVideoId,
        );
        const video =
          selected?.value.kind === "project-video" &&
          hasAvailableMedia(mediaItems, selected.value.id, "video/mp4");
        const run = runs[shot.id] ?? "idle";
        return (
          <article
            className="episode-video-shot"
            key={shot.id}
            data-shot-id={shot.id}
          >
            <header>
              <div>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <h3>{shot.title}</h3>
              </div>
              <strong>{reviewText[shot.videoReview]}</strong>
            </header>
            <div className="episode-video-columns">
              <div className="episode-video-input">
                <h4>本镜输入</h4>
                <ImagePreview
                  media={frame?.value}
                  label={`${shot.title}当前首帧`}
                  projectId={projectId}
                  mediaItems={mediaItems}
                />
                <p>
                  {shot.description} {shot.action}
                </p>
                <p>
                  引用素材：
                  {shot.assetIds
                    .map(
                      (id) =>
                        value.assets.find((asset) => asset.id === id)?.name ??
                        "素材已删除",
                    )
                    .join("、") || "无"}
                </p>
                <p>
                  {frame
                    ? `${frame.value.kind === "project-image" ? "项目图片" : "演示图片"} · 已选首帧`
                    : "尚未选择首帧"}
                </p>
                {!gate.ok && (
                  <p className="episode-video-reason" role="status">
                    {gate.reason}
                  </p>
                )}
                {frame?.value.kind === "project-image" && !mediaChecked && (
                  <p className="episode-help">正在核对项目图片状态…</p>
                )}
                <p className="episode-help">
                  计划时长 {shot.plannedMs} ms · 尾帧仅供支持该输入的模型参考。
                </p>
                <div className="episode-video-actions">
                  <button
                    type="button"
                    className="episode-primary-action"
                    disabled={
                      readOnly ||
                      !ready ||
                      Object.values(runs).some(
                        (status) =>
                          status === "preparing" || status === "running",
                      ) ||
                      !gate.ok
                    }
                    onClick={() => void generate(shot)}
                  >
                    {run === "running" ? "演示运行中…" : "生成演示视频"}
                  </button>
                  <button
                    type="button"
                    disabled={
                      readOnly ||
                      !ready ||
                      !projectId ||
                      Object.values(runs).some(
                        (status) =>
                          status === "preparing" || status === "running",
                      )
                    }
                    onClick={() => void importVideo(shot)}
                  >
                    导入项目 MP4
                  </button>
                </div>
                {run === "failed" && (
                  <p className="episode-video-reason" role="alert">
                    演示尝试未完成；保留以前的候选，可检查首帧后重试。
                  </p>
                )}
                {messages[shot.id] && (
                  <p className="episode-video-reason" role="status">
                    {messages[shot.id]}
                  </p>
                )}
              </div>
              <div className="episode-video-output">
                <h4>本镜候选</h4>
                {!shot.videos.length && (
                  <p className="episode-help">
                    尚无候选。确认单幅首帧后可生成交互演示。
                  </p>
                )}
                {shot.videos.length > 0 && (
                  <div className="episode-video-choices">
                    {shot.videos.map((candidate, candidateIndex) => (
                      <button
                        type="button"
                        key={candidate.id}
                        disabled={readOnly}
                        className={
                          candidate.id === shot.selectedVideoId
                            ? "selected"
                            : ""
                        }
                        onClick={() =>
                          onChange({
                            ...value,
                            shots: value.shots.map((item) =>
                              item.id === shot.id
                                ? {
                                    ...item,
                                    selectedVideoId: candidate.id,
                                    videoReview: "review",
                                  }
                                : item,
                            ),
                            reviews: { ...value.reviews, video: "review" },
                          })
                        }
                      >
                        候选 {candidateIndex + 1} ·{" "}
                        {candidate.value.kind === "project-video"
                          ? "项目 MP4"
                          : "交互演示"}
                      </button>
                    ))}
                  </div>
                )}
                {selected?.value.kind === "demo-motion" && (
                  <div
                    className="episode-motion-preview"
                    role="img"
                    aria-label={`${shot.title}模拟位移动效，非视频文件`}
                  >
                    <span>演示动作预览</span>
                    <i aria-hidden="true" />
                    <small>交互演示 · 非视频文件</small>
                  </div>
                )}
                {selected?.value.kind === "project-video" &&
                  (video && projectId ? (
                    <video
                      controls
                      preload="metadata"
                      src={mediaUrl(projectId, selected.value.id) ?? undefined}
                      aria-label={`${shot.title}项目视频`}
                    />
                  ) : (
                    <p className="episode-video-reason">
                      项目 MP4 不可用，请刷新媒体状态。
                    </p>
                  ))}
                {selected && (
                  <button
                    type="button"
                    disabled={
                      readOnly ||
                      !gate.ok ||
                      (selected.value.kind === "project-video" && !video)
                    }
                    onClick={() =>
                      onChange(
                        confirmVideo(value, shot.id, selected.id, mediaItems),
                      )
                    }
                  >
                    确认本镜视频
                  </button>
                )}
              </div>
            </div>
          </article>
        );
      })}
      {completed && (
        <div className="episode-complete" role="status">
          <strong>演示流程完成，非成片</strong>
          <p>所有镜头候选已逐一确认；本页不包含音频、剪辑或导出。</p>
        </div>
      )}
      {readOnly && (
        <p className="episode-help">
          只读模式可查看已保存候选，不能生成、导入或确认。
        </p>
      )}
    </>
  );
}
