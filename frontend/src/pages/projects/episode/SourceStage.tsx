import { EpisodeModelSelect } from "../../../features/projects/EpisodeModelSelect";
import { Button } from "antd";
import React from "react";
import {
  sampleScript,
} from "../../../features/projects/episode-demo";
import {
  editScript,
  type EpisodeWorkflow,
} from "../../../features/projects/episode-workflow";

export function SourceStage({
  value,
  readOnly,
  onChange,
}: {
  value: EpisodeWorkflow;
  readOnly: boolean;
  onChange: (next: EpisodeWorkflow) => void;
}) {
  function generate() {
    const candidate = sampleScript(value.novel);
    if (!candidate) return;
    onChange({
      ...value,
      scriptCandidates: [
        ...value.scriptCandidates,
        { ...candidate, id: crypto.randomUUID() },
      ],
      reviews: { ...value.reviews, source: "review" },
    });
  }

  function select(id: string) {
    const candidate = value.scriptCandidates.find((item) => item.id === id);
    if (!candidate) return;
    if (
      value.scriptDraft.trim() &&
      value.scriptDraft !== candidate.value &&
      !globalThis.confirm("选用候选会替换当前剧本编辑稿。是否继续？")
    )
      return;
    onChange(editScript(value, candidate.value));
  }

  return (
    <>
      <div className="episode-stage-heading">
        <div>
          <h2>小说与剧本生成</h2>
          <p>原文和剧本独立保存。演示候选仅作写作起点，请核对并编辑。</p>
        </div>
        <span>{value.novel.length} 字</span>
      </div>
      <label className="episode-editor-label">
        本集小说
        <textarea
          className="episode-novel-text"
          rows={12}
          value={value.novel}
          readOnly={readOnly}
          placeholder="粘贴本集范围内的小说原文"
          onChange={(event) => {
            const dependent = editScript(value, value.scriptDraft);
            const hasScript = Boolean(
              value.scriptDraft ||
              value.approvedScript ||
              value.scriptCandidates.length,
            );
            onChange({
              ...dependent,
              novel: event.target.value,
              reviews: {
                ...dependent.reviews,
                source: "review",
                script: hasScript ? "stale" : value.reviews.script,
              },
            });
          }}
        />
      </label>
      <div className="episode-stage-controls episode-source-controls">
        <label>
          剧本生成模型
          <EpisodeModelSelect
            kind="text"
            label="剧本生成模型"
            value={value.models.script}
            disabled={readOnly}
            onChange={(id) => onChange({ ...value, models: { ...value.models, script: id } })}
          />
        </label>
        <Button
          type="primary"
          disabled={readOnly || !value.novel.trim()}
          onClick={generate}
        >
          演示生成剧本
        </Button>
      </div>
      {!value.novel.trim() && (
        <p className="episode-help">
          先输入本集小说，或在下一阶段直接粘贴已有剧本。
        </p>
      )}
      {value.scriptCandidates.length > 0 && (
        <div className="episode-candidates">
          <h3>剧本候选 · 演示</h3>
          {value.scriptCandidates.map((candidate) => (
            <article key={candidate.id} className="episode-candidate">
              <pre>{candidate.value}</pre>
              <Button
                type="primary"
                disabled={readOnly}
                onClick={() => select(candidate.id)}
              >
                选用此候选
              </Button>
            </article>
          ))}
        </div>
      )}
    </>
  );
}
