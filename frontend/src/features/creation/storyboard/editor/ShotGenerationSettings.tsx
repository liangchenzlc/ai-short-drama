import { Button } from 'antd';
import { X } from 'lucide-react';
import type { ReactNode } from 'react';
export function ShotGenerationSettings({
  open,
  active,
  onClose,
  children,
  id,
}: {
  open: boolean;
  active: boolean;
  onClose: () => void;
  children: ReactNode;
  id?: string;
}) {
  return (
    <div id={id} className="standard-object-settings" hidden={!open || !active} aria-label="分镜生成设置">
      <header className="shot-settings-heading">
        <h3>生成设置</h3>
        <Button
          type="text"
          aria-label="关闭分镜生成设置"
          title="关闭分镜生成设置"
          icon={<X size={16} />}
          onClick={onClose}
        />
      </header>
      <div className="shot-settings-scroll">{children}</div>
    </div>
  );
}
