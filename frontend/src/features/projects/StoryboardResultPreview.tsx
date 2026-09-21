import { Alert, Button } from 'antd';
import type { GenerationDetail } from '../../api/types/generations';

export function StoryboardResultPreview({ task, busy, error, onApply }: { task: GenerationDetail; busy: boolean; error?: string; onApply: (mode: 'append' | 'replace') => void }) {
  const result = task.result.business?.kind === 'script_shots' ? task.result.business : null;
  if (!result) return <Alert type="warning" message="此任务没有可应用的结构化分镜结果。原始任务仍可在任务中心查看。"/>;
  return <section className="storyboard-result-preview"><h3>生成候选 · {result.shots.length} 镜</h3>{result.applied && <Alert type="success" message={`已${result.applied.mode === 'append' ? '追加' : '替换'}到活动分镜`}/>}<ol>{result.shots.map((shot, index) => <li key={index}><p>{shot.script}</p><small>{shot.asset_ids.length ? `关联素材：${shot.asset_ids.join('、')}` : '未关联素材'}</small></li>)}</ol>{error && <Alert type="error" message={error}/>}<div className="dialog-actions"><Button type="primary" loading={busy} disabled={!!result.applied} onClick={() => onApply('append')}>追加到现有分镜</Button><Button danger loading={busy} disabled={!!result.applied} onClick={() => onApply('replace')}>替换当前分镜…</Button></div></section>;
}
