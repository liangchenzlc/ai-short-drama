import type { CreationStage as StageId } from '@host/types/creation/workspace/model';
import { Tooltip } from 'antd';
import { Clapperboard, FilePenLine, Images, MonitorPlay } from 'lucide-react';
const steps = [
  { id: 'source', label: '剧本创作', icon: FilePenLine },
  { id: 'assets', label: '资产生成', icon: Images },
  { id: 'storyboard', label: '分镜制作', icon: Clapperboard },
  { id: 'assembly', label: '成片预览', icon: MonitorPlay },
] as const;
export function TopStepNav({
  currentStage,
  onStageChange,
}: {
  currentStage: StageId;
  onStageChange: (stage: StageId) => void;
}) {
  return (
    <nav className="creation-stage-nav" aria-label="创作流程">
      {steps.map(({ id, label, icon: StageIcon }) => (
        <Tooltip key={id} title={label} placement="right" trigger={['hover', 'focus']}>
          <button
            type="button"
            className="creation-stage-link"
            aria-current={currentStage === id ? 'page' : undefined}
            onClick={() => onStageChange(id)}
            aria-label={label}
          >
            <StageIcon size={18} aria-hidden="true" />
          </button>
        </Tooltip>
      ))}
    </nav>
  );
}
