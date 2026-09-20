import { Button } from "antd";
import { sampleShots } from "../../../features/projects/episode-demo";
import {
  editShot,
  type AssetItem,
  type EpisodeWorkflow,
} from "../../../features/projects/episode-workflow";
import type { ListedMedia } from "../../../features/projects/episode-media";
import { ImagePreview } from "./ImagePreview";
import { ShotProductionTable } from "./ShotProductionTable";
import { shotScript } from "../../../features/projects/shot-generation-settings";

export {
  confirmShotText,
  confirmStoryboard,
  reorderStoryboardShots,
  addStoryboardShot,
  removeStoryboardShot,
  createStoryboardGrids,
  bindStoryboardGridCell,
  confirmStoryboardGridCell,
  adoptGridCellAsFirstFrame,
} from "./storyboard-legacy";

const assetKinds = [
  { kind: "character", label: "角色" },
  { kind: "scene", label: "场景" },
  { kind: "prop", label: "道具" },
] as const;

function AssetThumbnail({
  asset,
  projectId,
  mediaItems,
}: {
  asset: AssetItem;
  projectId?: string;
  mediaItems: readonly ListedMedia[];
}) {
  const image = asset.imageCandidates.find(
    (candidate) => candidate.id === asset.selectedImageId,
  )?.value;
  return (
    <span className="storyboard-asset-thumbnail">
      {image ? (
        <ImagePreview
          media={image}
          label={asset.name}
          projectId={projectId}
          mediaItems={mediaItems}
          kind={asset.kind}
        />
      ) : (
        <span className="storyboard-thumbnail-empty">暂无图片</span>
      )}
    </span>
  );
}

export function StoryboardStage({
  value,
  readOnly,
  onChange,
  projectId,
  mediaItems = [],
}: {
  value: EpisodeWorkflow;
  readOnly: boolean;
  onChange: (next: EpisodeWorkflow) => void;
  projectId?: string;
  mediaItems?: readonly ListedMedia[];
}) {
  const update = (next: EpisodeWorkflow) => {
    if (!readOnly) onChange(next);
  };
  const generate = () => {
    if (readOnly || !value.scriptDraft.trim()) return;
    if (
      value.shots.length > 0 &&
      !window.confirm(
        "重新生成将替换本集全部分镜脚本和素材关联，并清除这些镜头对应的分镜图及视频结果。是否继续？",
      )
    )
      return;
    update({
      ...value,
      shots: sampleShots(value.scriptDraft, value.assets).map((shot) => ({
        ...shot,
        id: crypto.randomUUID(),
      })),
      gridBatches: [],
      reviews: { ...value.reviews, storyboard: "review", video: "not_started" },
    });
  };

  return (
    <div className="storyboard-workspace">
      <div className="episode-stage-heading storyboard-heading">
        <div>
          <h2>分镜制作</h2>
          <p>逐镜编排脚本，查看分镜图与视频，保持创作上下文。</p>
        </div>
        <Button
          type="primary"
          disabled={readOnly || !value.scriptDraft.trim()}
          onClick={generate}
        >
          生成分镜脚本
        </Button>
      </div>
      <p className="storyboard-context">
        {value.shots.length
          ? `共 ${value.shots.length} 个分镜 · 点击条目展开`
          : "生成后将在这里展示分镜列表"}
        <span>当前为前端演示，图片与视频生成暂未开放。</span>
      </p>

      {!value.shots.length ? (
        <div className="storyboard-empty">
          <h3>从剧本开始编排镜头</h3>
          <p>
            {value.scriptDraft.trim()
              ? "点击「生成分镜脚本」，查看镜头描述并关联本集素材。"
              : "请先在「剧本确认与素材拆解」中填写本集剧本。"}
          </p>
        </div>
      ) : (
        <div className="storyboard-list" aria-label="分镜脚本列表">
          {value.shots.map((shot, index) => (
            <details className="storyboard-item" key={shot.id} open={index === 0}>
              <summary className="storyboard-summary">
                <span className="storyboard-summary-copy">
                  <strong>分镜镜头 {index + 1}</strong>
                  <span>
                    {(shot.script ?? shot.description) || "暂无分镜描述"}
                  </span>
                </span>
                <svg
                  className="storyboard-chevron"
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                >
                  <path d="m9 5 7 7-7 7" />
                </svg>
              </summary>
              <div className="storyboard-expanded storyboard-shot-layout">
<div className="storyboard-script-column">
                <section className="storyboard-script">
                  <label htmlFor={`shot-script-${shot.id}`}>分镜脚本</label>
                  <textarea
                    id={`shot-script-${shot.id}`}
                    rows={4}
                    value={shotScript(shot)}
                    readOnly={readOnly}
                    placeholder="描述本镜画面、人物动作与对白…"
                    aria-describedby={`shot-script-note-${shot.id}`}
                    onChange={(event) =>
                      update(
                        editShot(value, shot.id, {
                          script: event.target.value,
                        }),
                      )
                    }
                  />
                  <p id={`shot-script-note-${shot.id}`}>
                    修改后自动保存，请核对已有图片与视频。
                  </p>
                </section>
                <section
                  className="storyboard-materials"
                  aria-label={`分镜镜头 ${index + 1}关联素材`}
                >
                  <div
                    className="storyboard-materials-table"
                    aria-label={`分镜镜头 ${index + 1}关联素材分类`}
                  >
                    
                      
                        {assetKinds.map(({ kind, label }) => {
                          const available = value.assets.filter(
                            (asset) => asset.kind === kind,
                          );
                          const linked = available.filter((asset) =>
                            shot.assetIds.includes(asset.id),
                          );
                          const remaining = available.filter(
                            (asset) => !shot.assetIds.includes(asset.id),
                          );
                          return (
                            <div className="storyboard-asset-group" key={kind}>
                              <h4>{label}</h4>
                              <div className="storyboard-asset-row">
                                {linked.map((asset) => (
                                  <div
                                    className="storyboard-linked-asset"
                                    key={asset.id}
                                  >
                                    <AssetThumbnail
                                      asset={asset}
                                      projectId={projectId}
                                      mediaItems={mediaItems}
                                    />
                                    <span
                                      className="storyboard-asset-name"
                                      title={asset.name}
                                    >
                                      {asset.name || "未命名" + label}
                                    </span>
                                    {!readOnly && (
                                      <button
                                        className="storyboard-unlink"
                                        type="button"
                                        aria-label={`取消关联${asset.name || label}`}
                                        onClick={() =>
                                          update(
                                            editShot(value, shot.id, {
                                              assetIds: shot.assetIds.filter(
                                                (id) => id !== asset.id,
                                              ),
                                            }),
                                          )
                                        }
                                      >
                                        <svg
                                          viewBox="0 0 16 16"
                                          aria-hidden="true"
                                        >
                                          <path d="m4 4 8 8m0-8-8 8" />
                                        </svg>
                                      </button>
                                    )}
                                  </div>
                                ))}
                                {linked.length === 0 && (
                                  <span className="storyboard-no-assets">
                                    未关联{label}
                                  </span>
                                )}
                              </div>
                              {!readOnly && (
                                <details className="storyboard-asset-picker">
                                  <summary>添加关联{label}</summary>
                                  {remaining.length ? (
                                    <div className="storyboard-asset-options">
                                      {remaining.map((asset) => (
                                        <button
                                          type="button"
                                          className="storyboard-asset-option"
                                          key={asset.id}
                                          onClick={() =>
                                            update(
                                              editShot(value, shot.id, {
                                                assetIds: [
                                                  ...new Set([
                                                    ...shot.assetIds,
                                                    asset.id,
                                                  ]),
                                                ],
                                              }),
                                            )
                                          }
                                        >
                                          <AssetThumbnail
                                            asset={asset}
                                            projectId={projectId}
                                            mediaItems={mediaItems}
                                          />
                                          <span>
                                            {asset.name || "未命名" + label}
                                          </span>
                                        </button>
                                      ))}
                                    </div>
                                  ) : (
                                    <p>
                                      {available.length
                                        ? "本集" + label + "已全部关联"
                                        : "暂无可关联" +
                                          label +
                                          "，请先在素材图片步骤添加。"}
                                    </p>
                                  )}
                                </details>
                              )}
                            </div>
                          );
                        })}
                      
                    
                  </div>
                </section>
                </div>
                <ShotProductionTable
                  value={value}
                  shot={shot} shotLabel={`分镜镜头 ${index + 1}`}
                  readOnly={readOnly}
                  onChange={update}
                  projectId={projectId}
                  mediaItems={mediaItems}
                />
              </div>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}

