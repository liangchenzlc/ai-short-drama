import { Fragment, useEffect, useRef } from 'react';
import type { EpisodeNavigationStage as StageId } from '../../../features/projects/episode-workflow';

const steps: { id: StageId; label: string }[] = [
  { id: 'source', label: '剧本创作' }, { id: 'assets', label: '素材生成' },
  { id: 'storyboard', label: '分镜制作' }, { id: 'assembly', label: '成片合成' },
];
export function TopStepNav({ currentStage, onStageChange }: {
  currentStage: StageId; onStageChange: (stage: StageId) => void;
}) {
  const navigation = useRef<HTMLElement>(null);
  useEffect(() => {
    const reveal = () => {
      const nav = navigation.current;
      const current = nav?.querySelector<HTMLElement>('[aria-current="step"]');
      if (!nav || !current) return;
      const frame = nav.getBoundingClientRect(), button = current.getBoundingClientRect();
      if (button.left < frame.left + 9 || button.right > frame.right - 9) nav.scrollLeft += button.left - frame.left - 9;
    };
    reveal(); window.addEventListener('resize', reveal);
    return () => window.removeEventListener('resize', reveal);
  }, [currentStage]);
  return <nav ref={navigation} className="episode-top-step-nav" aria-label="创作流程">{steps.map((step, index) => {
    return <Fragment key={step.id}>{index > 0 && <span className="episode-step-connector" aria-hidden="true"/>}<button type="button" className={`episode-step-button${currentStage === step.id ? ' active' : ''}`}
      aria-current={currentStage === step.id ? 'step' : undefined} onClick={() => onStageChange(step.id)} aria-label={step.label}>
      <span className="episode-step-number">{String(index + 1).padStart(2, '0')}</span><span className="episode-step-text"><strong className="episode-step-name">{step.label}</strong>
      </span>
    </button></Fragment>;
  })}</nav>;
}
