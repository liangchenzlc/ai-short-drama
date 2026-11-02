import { MediaLimitFields } from '@host/features/assistant/settings/MediaLimitFields';
import type { TaskMediaLimitsProps } from '@host/types/assistant/composer/TaskMediaLimits';
import { Button, Checkbox, Popover } from 'antd';
import { useState } from 'react';

export function TaskMediaLimits({ value, frozen, onChange, disabled }: TaskMediaLimitsProps) {
  const [open, setOpen] = useState(false);
  const limits = frozen ?? value;
  return <Popover open={open} onOpenChange={setOpen} trigger="click" placement="topRight" content={<section className="assistant-task-media-limits" aria-label="本任务媒体数量">
    {!frozen && <Checkbox checked={value !== undefined} disabled={disabled} onChange={event => onChange(event.target.checked ? { images: null, videos: null, audio: null } : undefined)}>为本任务覆盖数量</Checkbox>}
    {limits !== undefined && <MediaLimitFields value={limits} onChange={onChange} disabled={disabled || !!frozen} />}
    <p>{frozen ? '当前任务数量已固定，开始新任务后可调整。' : value === undefined ? '省略覆盖，继承账号默认数量。' : '留空表示不限，0 表示禁止该类生成。'}</p>
    <Button size="small" onClick={() => setOpen(false)}>完成</Button>
  </section>}><Button type="text" size="small" disabled={disabled} aria-label="媒体数量">媒体数量{frozen ? ' · 已固定' : value !== undefined ? ' · 已覆盖' : ''}</Button></Popover>;
}
