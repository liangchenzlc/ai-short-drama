import type { ProjectSession } from "../../types/projects";
import { Icon } from '../../components/ui/Icon';
import { Button } from "antd";
import { AccountControls } from '../auth/AccountControls';

export function ProjectDetailHeader({
  session,
  onBack,
  onClose,
  canClose,
  fallbackTitle = '正在打开项目',
}: {
  session: ProjectSession | null;
  onBack: () => void;
  onClose: () => void;
  canClose: boolean;
  fallbackTitle?: string;
}) {
  return (
    <header className="detail-header">
      <div className="detail-header-inner">
        <Button type="link" className="detail-back" icon={<Icon name="back" size={16}/>} onClick={onBack}>
          返回项目管理
        </Button>
        <div className="detail-title">
          <span>项目详情</span>
          <h1>{session?.project.name ?? fallbackTitle}</h1>
        </div>
        <div className="detail-header-actions">
          <Button className="detail-close" disabled={!canClose} onClick={onClose}>关闭项目</Button>
          <AccountControls />
        </div>
      </div>
    </header>
  );
}
