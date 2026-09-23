import { Button } from 'antd';
import type { EpisodeWorkflow } from '../../../features/projects/episode-workflow';

export function ScriptStage({ value, script, confirmed, readOnly, busy, onEdit, onConfirm, onContinue, projectAspect }: {
  value: EpisodeWorkflow; script: string; confirmed: boolean; readOnly: boolean; busy: boolean;
  onEdit: (content: string) => void; onConfirm: () => void;
  onContinue: () => void;
  projectAspect?: '16:9' | '9:16';
}) {
  return <>
    <div className="episode-stage-heading"><div><h2>剧本编辑与确认</h2><p>自动保存保留草稿；确认后再次修改剧本，需要重新确认。</p></div><span className={`status-badge ${confirmed ? 'is-success' : 'is-pending'}`}>{confirmed ? '已确认' : '待确认'}</span></div>
    <label className="episode-editor-label">本集剧本<textarea className="episode-script-textarea" rows={18} value={script} readOnly={readOnly} placeholder="粘贴或编写本集剧本" onChange={event => onEdit(event.target.value)}/></label>
    <dl className="script-context"><div><dt>本集画幅</dt><dd>{value.aspect === '16:9' ? '横屏 16:9' : '竖屏 9:16'}</dd></div><div><dt>视觉风格</dt><dd>{value.style || '尚未设置'}</dd></div></dl>
    <p className="episode-help">画幅和风格可在左侧 AI 功能栏调整。</p>
    {projectAspect && projectAspect !== value.aspect && <p className="episode-help">本集画幅与项目设置不同；后续画面需保持本集画幅一致。</p>}
    <div className="episode-script-action-row"><div><strong>{confirmed ? '剧本已定稿，可以准备素材了' : '确认剧本，再开始制作'}</strong><p>{confirmed ? '后续修改仍会保存，修改后需重新确认。' : '确认后可生成分镜。此操作不会调用 AI。'}</p></div>{confirmed ? <Button type="primary" onClick={onContinue}>准备素材</Button> : <Button type="primary" loading={busy} disabled={readOnly || busy || !script.trim()} onClick={onConfirm}>确认当前剧本</Button>}</div>
  </>;
}
