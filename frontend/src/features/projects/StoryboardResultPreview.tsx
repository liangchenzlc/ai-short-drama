import { Alert, Button } from 'antd';
import type { GenerationDetail } from '../../api/types/generations';
import { storyboardTiming } from './workflow-contract';

const seconds = (milliseconds: number) => `${Number((milliseconds / 1000).toFixed(1))} 秒`;

export function StoryboardResultPreview({ task, busy, error, assetNames = {}, onApply }: {
  task: GenerationDetail;
  busy: boolean;
  error?: string;
  assetNames?: Record<string, string>;
  onApply: (mode: 'append' | 'replace') => void;
}) {
  const result = task.result.business?.kind === 'script_shots' ? task.result.business : null;
  if (!result) return <Alert type="warning" message="此任务没有可应用的结构化分镜结果。原始任务仍可在任务中心查看。"/>;
  const timing = storyboardTiming(result.shots);
  return <section className="storyboard-result-preview">
    <header className="storyboard-result-heading">
      <div><h3>生成候选 · {result.shots.length} 镜</h3><p>预计总时长 {seconds(timing.total_ms)}，平均每镜 {seconds(timing.average_ms)}</p></div>
    </header>
    {result.applied && <Alert type="success" message={`已${result.applied.mode === 'append' ? '追加' : '替换'}到活动分镜`}/>}
    <ol>{result.shots.map((shot, index) => {
      const duration = shot.duration_ms ?? 3000;
      return <li key={index}>
        <article>
          <header><strong>{shot.title?.trim() || `分镜 ${index + 1}`}</strong><span>{seconds(duration)}</span></header>
          {shot.story_beat && <div className="storyboard-beat"><h4>叙事节拍</h4><p>{shot.story_beat}</p></div>}
          {shot.source_excerpt && <blockquote><span>原文依据</span>{shot.source_excerpt}</blockquote>}
          <div className="storyboard-shot-copy"><h4>镜头脚本</h4><p>{shot.script}</p></div>
          <small>{shot.asset_ids.length ? `关联素材：${shot.asset_ids.map(id => assetNames[id] ? `${assetNames[id]}（${id}）` : id).join('、')}` : '未关联素材'}</small>
        </article>
      </li>;
    })}</ol>
    {error && <Alert type="error" message={error}/>}
    <div className="dialog-actions"><Button type="primary" loading={busy} disabled={!!result.applied} onClick={() => onApply('append')}>追加到现有分镜</Button><Button danger loading={busy} disabled={!!result.applied} onClick={() => onApply('replace')}>替换当前分镜…</Button></div>
  </section>;
}
