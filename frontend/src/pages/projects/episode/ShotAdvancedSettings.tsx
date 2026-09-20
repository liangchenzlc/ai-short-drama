import { useState } from "react";
import type {
  EpisodeWorkflow,
  ShotItem,
} from "../../../features/projects/episode-workflow";
import {
  imageAspects,
  imageDimensions,
  imageLayouts,
  imageResolutions,
  imageSettingsForShot,
  videoSettingsForShot,
  updateShotGenerationSettings,
  type ShotImageSettings,
  type ShotVideoSettings,
} from "../../../features/projects/shot-generation-settings";

type Props = {
  value: EpisodeWorkflow;
  shot: ShotItem;
  readOnly: boolean;
  onChange: (next: EpisodeWorkflow) => void;
};

export function ImageAdvancedSettings({
  value,
  shot,
  readOnly,
  onChange,
}: Props) {
  const settings = imageSettingsForShot(shot);
  const aspect = settings.aspect === "inherit" ? value.aspect : settings.aspect;
  const dimensions = imageDimensions(settings.resolution, aspect);
  const layout = imageLayouts.find((item) => item.value === settings.layout)!;
  const update = (patch: Partial<ShotImageSettings>) => {
    if (!readOnly)
      onChange(
        updateShotGenerationSettings(value, shot.id, {
          imageSettings: { ...settings, ...patch },
        }),
      );
  };
  return (
    <details className="shot-advanced-settings">
      <summary aria-label="图片高级设置">
        <span>高级设置</span>
        <span className="shot-settings-summary">
          {settings.resolution} / {aspect} / {layout.label}
        </span>
      </summary>
      <div className="shot-settings-body">
        <div className="shot-settings-fields">
          <label>
            <span id={`shot-image-resolution-${shot.id}`}>画面分辨率</span>
            <select
              aria-labelledby={`shot-image-resolution-${shot.id}`}
              value={settings.resolution}
              disabled={readOnly}
              onChange={(event) =>
                update({
                  resolution: event.target
                    .value as ShotImageSettings["resolution"],
                })
              }
            >
              {imageResolutions.map((resolution) => {
                const size = imageDimensions(resolution, aspect);
                return (
                  <option key={resolution} value={resolution}>
                    {resolution}（{size.width} × {size.height}）
                  </option>
                );
              })}
            </select>
          </label>
          <label>
            <span id={`shot-image-aspect-${shot.id}`}>画面比例</span>
            <select
              aria-labelledby={`shot-image-aspect-${shot.id}`}
              value={settings.aspect}
              disabled={readOnly}
              onChange={(event) =>
                update({
                  aspect: event.target.value as ShotImageSettings["aspect"],
                })
              }
            >
              <option value="inherit">跟随本集（{value.aspect}）</option>
              {imageAspects.map((ratio) => (
                <option key={ratio} value={ratio}>
                  {ratio}
                </option>
              ))}
            </select>
          </label>
        </div>
        <fieldset className="shot-layout-options" disabled={readOnly}>
          <legend>图片布局</legend>
          <div>
            {imageLayouts.map((item) => (
              <label
                key={item.value}
                className={settings.layout === item.value ? "is-selected" : ""}
              >
                <input
                  type="radio"
                  name={`shot-image-layout-${shot.id}`}
                  value={item.value}
                  checked={settings.layout === item.value}
                  onChange={() => update({ layout: item.value })}
                />
                <span
                  className={`shot-layout-diagram layout-${item.value}`}
                  aria-hidden="true"
                >
                  {Array.from({ length: item.cells }, (_, index) => (
                    <i key={index} />
                  ))}
                </span>
                <span>{item.label}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <p className="shot-settings-note">
          整图预设 {dimensions.width} × {dimensions.height}
          ；实际尺寸以模型支持为准。
          {settings.layout !== "single" &&
            `宫格展示本镜连续动作${settings.layout === "five" ? "，上二下三排列" : ""}，视频制作前需选取单格。`}
        </p>
      </div>
    </details>
  );
}

function DurationInput({
  durationMs,
  readOnly,
  id,
  onCommit,
}: {
  durationMs: number;
  readOnly: boolean;
  id: string;
  onCommit: (value: number) => void;
}) {
  const [draft, setDraft] = useState(String(durationMs / 1000));
  const seconds = Number(draft);
  const valid =
    draft.trim() !== "" &&
    Number.isFinite(seconds) &&
    seconds > 0 &&
    Number.isSafeInteger(Math.round(seconds * 1000)) &&
    Math.round(seconds * 1000) > 0;
  return (
    <label>
      <span id={`${id}-duration-label`}>时长（秒）</span>
      <input
        aria-labelledby={`${id}-duration-label`}
        type="number"
        min="0.001"
        step="any"
        inputMode="decimal"
        value={draft}
        readOnly={readOnly}
        aria-invalid={!valid}
        aria-describedby={`${id}-duration-note`}
        onChange={(event) => {
          const next = event.target.value;
          setDraft(next);
          const milliseconds = Math.round(Number(next) * 1000);
          if (
            !readOnly &&
            next.trim() &&
            Number.isSafeInteger(milliseconds) &&
            milliseconds > 0
          )
            onCommit(milliseconds);
        }}
      />
      <span
        id={`${id}-duration-note`}
        className={valid ? "shot-field-hint" : "shot-field-error"}
      >
        {valid ? "生成请求时长" : "请输入大于 0 的有效秒数；此修改尚未保存。"}
      </span>
    </label>
  );
}

export function VideoAdvancedSettings({
  value,
  shot,
  readOnly,
  onChange,
}: Props) {
  const settings = videoSettingsForShot(shot);
  const update = (patch: Partial<ShotVideoSettings>) => {
    if (!readOnly)
      onChange(
        updateShotGenerationSettings(value, shot.id, {
          videoSettings: { ...settings, ...patch },
        }),
      );
  };
  return (
    <details className="shot-advanced-settings">
      <summary aria-label="视频高级设置">
        <span>高级设置</span>
        <span className="shot-settings-summary">
          {settings.durationMs / 1000} 秒 / {settings.resolution.toUpperCase()}
        </span>
      </summary>
      <div className="shot-settings-body">
        <div className="shot-settings-fields">
          <DurationInput
            key={shot.id}
            id={shot.id}
            durationMs={settings.durationMs}
            readOnly={readOnly}
            onCommit={(durationMs) => update({ durationMs })}
          />
          <label>
            <span id={`shot-video-resolution-${shot.id}`}>画面分辨率</span>
            <select
              aria-labelledby={`shot-video-resolution-${shot.id}`}
              value={settings.resolution}
              disabled={readOnly}
              onChange={(event) =>
                update({
                  resolution: event.target
                    .value as ShotVideoSettings["resolution"],
                })
              }
            >
              <option value="720p">720P</option>
              <option value="1080p">1080P</option>
            </select>
          </label>
        </div>
        <p className="shot-settings-note">
          时长与分辨率为生成预设，后续按模型能力提供可用选项；已有视频会保留。
        </p>
      </div>
    </details>
  );
}
