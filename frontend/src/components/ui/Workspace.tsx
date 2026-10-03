import type { ReactNode } from 'react';

/** Common page chrome; feature-specific controls remain with their owners. */
export function PageHeader({ title, id, description, actions }: {
  title: string;
  id?: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return <header className="studio-page-head">
    <div className="page-heading"><h1 id={id}>{title}</h1>{description && <p>{description}</p>}</div>
    {actions && <div className="page-actions">{actions}</div>}
  </header>;
}

export function ListToolbar({ count, hint, actions }: {
  count: ReactNode;
  hint?: ReactNode;
  actions: ReactNode;
}) {
  return <div className="generation-list-toolbar">
    <p>{count}{hint && <span>{hint}</span>}</p>
    <div className="list-toolbar-actions">{actions}</div>
  </div>;
}
