import { Button, Tooltip, type ButtonProps } from 'antd';
import type { ReactNode } from 'react';
/** Compact controls keep their accessible name when the visible label is an icon. */
export function AssemblyAction({ label, icon, className, ...props }: ButtonProps & { label: string; icon: ReactNode }) {
  return (
    <Tooltip title={label} trigger={['hover', 'focus']}>
      <Button
        type="text"
        size="small"
        {...props}
        aria-label={label}
        icon={icon}
        className={`assembly-icon-action${className ? ` ${className}` : ''}`}
      />
    </Tooltip>
  );
}
