import type { ProjectSession } from "../../types/projects";
import { Button } from "antd";

export function ProjectDetailHeader({
  session,
  onBack,
  onClose,
  canClose,
}: {
  session: ProjectSession | null;
  onBack: () => void;
  onClose: () => void;
  canClose: boolean;
}) {
  return (
    <header className="detail-header">
      <div className="detail-header-inner">
        <Button type="link" className="detail-back" onClick={onBack}>
          ← 返回项目管理
        </Button>
        <div className="detail-title">
          <span>项目详情</span>
          <h1>{session?.project.name ?? "正在打开项目"}</h1>
        </div>
        <Button className="detail-close" disabled={!canClose} onClick={onClose}>
          关闭项目
        </Button>
      </div>
    </header>
  );
}
