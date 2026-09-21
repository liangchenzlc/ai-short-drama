import { Button } from 'antd';
import { EpisodeModelSelect } from '../../../features/projects/EpisodeModelSelect';
import type { EpisodeWorkflow } from '../../../features/projects/episode-workflow';

export function ScriptStage({ value, script, confirmed, readOnly, busy, onChange, onEdit, onConfirm, projectAspect }: {
  value: EpisodeWorkflow; script: string; confirmed: boolean; readOnly: boolean; busy: boolean;
  onChange: (next: EpisodeWorkflow) => void; onEdit: (content: string) => void; onConfirm: () => void;
  projectAspect?: '16:9' | '9:16';
}) {
  return <>
    <div className="episode-stage-heading"><div><h2>剧本编辑与确认</h2><p>自动保存保留草稿；确认后再次修改剧本，需要重新确认。</p></div><span>{confirmed ? '当前剧本已确认' : '未确认'}</span></div>
    <label className="episode-editor-label">本集剧本<textarea className="episode-script-textarea" rows={18} value={script} readOnly={readOnly} placeholder="粘贴或编写本集剧本" onChange={event => onEdit(event.target.value)}/></label>
    <div className="episode-script-settings">
      <label>画幅比例<select value={value.aspect} disabled><option value="16:9">16:9 横屏</option><option value="9:16">9:16 竖屏</option></select></label>
      <label>视觉风格<input value={value.style} readOnly /></label>
      <label>素材分析模型<EpisodeModelSelect kind="text" label="素材分析模型" value={value.models.analysis} disabled={readOnly} onChange={id => onChange({ ...value, models: { ...value.models, analysis: id } })}/></label>
    </div>
    <p className="episode-help">可返回项目详情，通过「编辑分集」修改本集画幅与风格。模型选择暂存于本浏览器。</p>
    {projectAspect && projectAspect !== value.aspect && <p className="episode-help">本集画幅与项目设置不同；后续画面需保持本集画幅一致。</p>}
    <div className="episode-script-action-row"><Button type="primary" loading={busy} disabled={readOnly || busy || !script.trim() || confirmed} onClick={onConfirm}>确认当前剧本</Button><p>确认只记录剧本状态。素材可在下一步维护；已确认剧本可生成服务端分镜候选。</p></div>
  </>;
}
