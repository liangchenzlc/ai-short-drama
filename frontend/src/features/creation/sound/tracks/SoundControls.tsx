import { Button, InputNumber, Slider, Tooltip } from 'antd';
import { CircleHelp } from 'lucide-react';
import './sound.css';

import type { SoundHelpProps, SoundVolumeProps } from '@host/types/creation/sound/editor';
export function SoundHelp({ title, label = '声音编辑说明' }: SoundHelpProps) {
  return (
    <Tooltip title={title} trigger={['hover', 'focus']}>
      <Button className="sound-help" type="text" size="small" icon={<CircleHelp size={15} />} aria-label={label} />
    </Tooltip>
  );
}
export function SoundVolume({ label, value, disabled, onChange }: SoundVolumeProps) {
  return (
    <div className="sound-volume">
      <span>{label}</span>
      <Slider
        aria-label={`${label}滑条`}
        disabled={disabled}
        min={0}
        max={2}
        step={0.05}
        value={value}
        onChange={onChange}
      />
      <InputNumber
        aria-label={label}
        disabled={disabled}
        min={0}
        max={2}
        step={0.05}
        value={value}
        onChange={(next) => onChange(next ?? 0)}
      />
    </div>
  );
}
