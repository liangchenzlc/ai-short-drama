import { useEffect, useState } from 'react';
import type { EpisodeNavigationStage as StageId, EpisodeWorkflow } from '../../../features/projects/episode-workflow';
import { assetLibraries } from '../../../api/modules/assets';
import { storyboardApi } from '../../../api/modules/storyboard';
import { assemblyApi } from '../../../api/modules/assembly';

const steps: { id: StageId; label: string }[] = [
  { id: 'source', label: '小说改编' }, { id: 'assets', label: '素材生成' },
  { id: 'storyboard', label: '分镜制作' }, { id: 'assembly', label: '成片合成' },
];
export function TopStepNav({ currentStage, workflow, onStageChange, projectId, episodeId }: {
  currentStage: StageId; workflow: EpisodeWorkflow; onStageChange: (stage: StageId) => void;
  projectId: string; episodeId: string;
}) {
  const [status, setStatus] = useState<Record<string, string>>({});
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const refresh = () => setRevision(value => value + 1);
    window.addEventListener('creation-workflow-updated', refresh);
    window.addEventListener('focus', refresh);
    return () => { window.removeEventListener('creation-workflow-updated', refresh); window.removeEventListener('focus', refresh); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const scope = { kind: 'episode' as const, projectId, episodeId };
    async function assets() {
      let offset = 0, total = 1, adopted = 0;
      while (offset < total) {
        const page = await assetLibraries.list(scope, { offset, limit: 100 }, controller.signal);
        total = page.total; adopted += page.items.filter(item => item.media_id).length;
        offset += page.items.length; if (!page.items.length) break;
      }
      return total ? `已采用 ${adopted}/${total}` : '待准备';
    }
    async function shots() {
      let offset = 0, total = 1, images = 0, videos = 0;
      let version: string | undefined;
      while (offset < total) {
        const page = await storyboardApi(projectId, episodeId).shots(controller.signal, false, offset, 100);
        if (version && version !== page.storyboard_version) throw new Error('分镜列表已变化，请重新读取状态。');
        version = page.storyboard_version;
        total = page.total; images += page.items.filter(item => item.image).length; videos += page.items.filter(item => item.video).length;
        offset += page.items.length; if (!page.items.length) break;
      }
      return total ? `图 ${images}/${total} · 视频 ${videos}/${total}` : '待制作';
    }
    void Promise.allSettled([assets(), shots(), assemblyApi(projectId, episodeId).get(controller.signal)]).then(results => {
      if (controller.signal.aborted) return;
      const [asset, shot, assembly] = results;
      setStatus({ assets: asset.status === 'fulfilled' ? asset.value : '状态未载入',
        storyboard: shot.status === 'fulfilled' ? shot.value : '状态未载入',
        assembly: assembly.status === 'fulfilled' ? assembly.value.current_work ? assembly.value.current_work.is_stale ? '需核对成片' : '已有成片' : '待合成' : '状态未载入' });
    });
    return () => controller.abort();
  }, [projectId, episodeId, currentStage, revision]);
  return <nav className="episode-top-step-nav" aria-label="分集制作流程">{steps.map((step, index) => {
    const label = step.id === 'source' ? workflow.reviews.script === 'confirmed' ? '剧本已确认' : '剧本待确认' : status[step.id] ?? '正在载入…';
    return <button key={step.id} type="button" className={`episode-step-button${currentStage === step.id ? ' active' : ''}`}
      aria-current={currentStage === step.id ? 'step' : undefined} onClick={() => onStageChange(step.id)} aria-label={`${step.label}，${label}`}>
      <span className="episode-step-number">{String(index + 1).padStart(2, '0')}</span><span className="episode-step-name">{step.label}</span>
      <span className="episode-step-status">{label}</span>
    </button>;
  })}</nav>;
}
