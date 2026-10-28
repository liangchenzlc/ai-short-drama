import { useAuth } from '@host/features/identity/session/AuthSession';
import { accountStorage } from '@host/features/identity/storage/account-storage';
import type { ResourceView } from '@host/types/creation/shared/resource-view/ResourceViewToggle';
import { Button, Tooltip } from 'antd';
import { LayoutGrid, List } from 'lucide-react';
import { useState } from 'react';
export function useResourceView(surface: 'assets' | 'storyboard') {
  const { user } = useAuth();
  const storage = accountStorage(user?.id ?? 'anonymous');
  const key = `creation:${surface}:view`;
  const [view, setView] = useState<ResourceView>(() => {
    try {
      return storage.getItem(key) === 'list' ? 'list' : 'grid';
    } catch {
      return 'grid';
    }
  });
  return [
    view,
    (next: ResourceView) => {
      setView(next);
      try {
        storage.setItem(key, next);
      } catch {
        /* The current view remains usable without storage. */
      }
    },
  ] as const;
}

export function ResourceViewToggle({
  value,
  onChange,
}: {
  value: ResourceView;
  onChange: (value: ResourceView) => void;
}) {
  return (
    <div className="resource-view-toggle" role="group" aria-label="展示方式">
      {[
        { key: 'list', label: '列表展示', icon: List },
        { key: 'grid', label: '宫格展示', icon: LayoutGrid },
      ].map(({ key, label, icon: ViewIcon }) => (
        <Tooltip key={key} title={label} trigger={['hover', 'focus']}>
          <Button
            type="text"
            aria-label={label}
            aria-pressed={value === key}
            icon={<ViewIcon size={15} aria-hidden="true" />}
            onClick={() => onChange(key as ResourceView)}
          />
        </Tooltip>
      ))}
    </div>
  );
}
