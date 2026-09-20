import { Button } from "antd";
import React from "react";
import {
  DEMO_MODELS,
  sampleAssets,
} from "../../../features/projects/episode-demo";
import {
  editScript,
  staleFrameReviews,
  type EpisodeWorkflow,
} from "../../../features/projects/episode-workflow";

export function ScriptStage({
  value,
  readOnly,
  onChange,
  projectAspect,
}: {
  value: EpisodeWorkflow;
  readOnly: boolean;
  onChange: (next: EpisodeWorkflow) => void;
  projectAspect?: "16:9" | "9:16";
}) {
  const currentSnapshot =
    value.reviews.script === "confirmed" &&
    value.approvedScript?.text === value.scriptDraft &&
    value.approvedScript.aspect === value.aspect &&
    value.approvedScript.style === value.style;
  function changeVisual(
    patch: Partial<Pick<EpisodeWorkflow, "aspect" | "style">>,
  ) {
    const hasAssetImages = value.assets.some(
      (item) => item.imageCandidates.length,
    );
    const hasFrames =
      value.shots.some(
        (shot) => shot.firstFrames.length || shot.endFrames.length,
      ) ||
      value.gridBatches.some((batch) => batch.cells.some((cell) => cell.ref));
    const hasVideos = value.shots.some((shot) => shot.videos.length);
    onChange({
      ...value,
      ...patch,
      assets: value.assets.map((asset) =>
        asset.imageCandidates.length ? { ...asset, review: "stale" } : asset,
      ),
      shots: value.shots.map((shot) => ({
        ...shot,
        ...staleFrameReviews(shot),
        frameReview:
          shot.firstFrames.length || shot.endFrames.length
            ? "stale"
            : shot.frameReview,
        videoReview: shot.videos.length ? "stale" : shot.videoReview,
      })),
      gridBatches: value.gridBatches.map((batch) => ({
        ...batch,
        cells: batch.cells.map((cell) =>
          cell.ref ? { ...cell, review: "stale" } : cell,
        ),
      })),
      reviews: {
        ...value.reviews,
        script: "review",
        assets: hasAssetImages ? "stale" : value.reviews.assets,
        storyboard: hasFrames ? "stale" : value.reviews.storyboard,
        video: hasVideos ? "stale" : value.reviews.video,
      },
    });
  }
  function generateFramework() {
    if (!value.scriptDraft.trim()) return;
    const existingKinds = new Set(value.assets.map((asset) => asset.kind));
    const additions = sampleAssets().filter(
      (asset) => !existingKinds.has(asset.kind),
    );
    onChange({
      ...value,
      approvedScript: {
        text: value.scriptDraft,
        aspect: value.aspect,
        style: value.style,
      },
      assets: [
        ...value.assets,
        ...additions.map((asset) => ({ ...asset, id: crypto.randomUUID() })),
      ],
      reviews: {
        ...value.reviews,
        script: "confirmed",
        assets: additions.length ? "review" : value.reviews.assets,
      },
    });
  }
  return (
    <>
      <div className="episode-stage-heading">
        <div>
          <h2>剧本确认与素材拆解</h2>
          <p>编辑剧本并设置画幅与风格，再生成可逐项修改的素材文本框架。</p>
        </div>
        <span>
          {currentSnapshot
            ? "当前剧本版本已保存"
            : value.reviews.script === "stale"
              ? "剧本变更待更新"
              : value.approvedScript
                ? "有待保存的修改"
                : "待生成文本框架"}
        </span>
      </div>
      <label className="episode-editor-label">
        本集剧本
        <textarea
          className="episode-script-textarea"
          rows={18}
          value={value.scriptDraft}
          readOnly={readOnly}
          placeholder="直接粘贴已有剧本，或选用上一阶段的演示候选"
          onChange={(event) => onChange(editScript(value, event.target.value))}
        />
      </label>
      <div className="episode-script-settings">
        <label>
          画幅比例
          <select
            value={value.aspect}
            disabled={readOnly}
            onChange={(event) =>
              changeVisual({ aspect: event.target.value as "16:9" | "9:16" })
            }
          >
            <option value="16:9">16:9 横屏</option>
            <option value="9:16">9:16 竖屏</option>
          </select>
        </label>
        <label>
          视觉风格
          <input
            value={value.style}
            readOnly={readOnly}
            placeholder="例如：写实、国风"
            onChange={(event) => changeVisual({ style: event.target.value })}
          />
        </label>
        <label>
          素材分析模型
          <select
            value={value.models.analysis}
            disabled={readOnly}
            onChange={(event) =>
              onChange({
                ...value,
                models: { ...value.models, analysis: event.target.value },
              })
            }
          >
            <option
              value={value.models.analysis}
              hidden={
                !DEMO_MODELS.analysis.some(
                  (item) => item.value === value.models.analysis,
                )
              }
            >
              {DEMO_MODELS.analysis.find((item) => item.value === value.models.analysis)?.label ?? value.models.analysis}
            </option>
            {DEMO_MODELS.analysis
              .filter((item) => item.value !== value.models.analysis)
              .map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
          </select>
        </label>
      </div>
      {projectAspect && projectAspect !== value.aspect && (
        <p className="episode-help">
          本集画幅与项目设置不同；后续画面需保持本集画幅一致。
        </p>
      )}
      <div className="episode-script-action-row">
        <Button
          type="primary"
          disabled={readOnly || !value.scriptDraft.trim()}
          onClick={generateFramework}
        >
          一键生成文本框架
        </Button>
        <p>
          {value.scriptDraft.trim()
            ? "同时保存当前剧本版本；演示模式仅补齐缺少的角色、场景和道具占位文本，不覆盖已有内容。"
            : "先输入或选用本集剧本，再生成角色、场景和道具的文本框架。"}
        </p>
      </div>
    </>
  );
}
