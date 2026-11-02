import type { MediaLimitFieldsProps } from '@host/types/assistant/settings/media-limits';
import { InputNumber } from 'antd';

export function MediaLimitFields({ value, onChange, disabled, prefix = '' }: MediaLimitFieldsProps) {
  return <div className="assistant-media-limit-fields">
    {(['images', 'videos', 'audio'] as const).map(kind => <label key={kind}>
      <span>{prefix}{{ images: '图片数量', videos: '视频数量', audio: '音频数量' }[kind]}</span>
      <InputNumber aria-label={`${prefix}${{ images: '图片数量', videos: '视频数量', audio: '音频数量' }[kind]}`}
        min={0} max={1000} precision={0} value={value[kind]} placeholder="不限" disabled={disabled}
        onChange={next => onChange({ ...value, [kind]: next })} />
    </label>)}
  </div>;
}
