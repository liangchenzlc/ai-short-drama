import type { ReactNode } from 'react';
import './setting-row.css';

export function SettingRow({ title, description, children, nested = false }: {
  title: string; description?: ReactNode; children: ReactNode; nested?: boolean;
}) {
  return <div className={`setting-row${nested ? ' setting-row-nested' : ''}`}>
    <div className="setting-row-copy"><span>{title}</span>{description && <p>{description}</p>}</div>
    <div className="setting-row-control">{children}</div>
  </div>;
}
