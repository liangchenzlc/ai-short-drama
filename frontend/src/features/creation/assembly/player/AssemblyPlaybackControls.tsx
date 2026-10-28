import { AssemblyAction } from '@host/features/creation/assembly/export/AssemblyAction';
import { framecode } from '@host/features/creation/assembly/sessions/assembly-editing';
import { Maximize, Pause, Play, StepBack, StepForward, Volume2, VolumeX } from 'lucide-react';
import type { ReactNode } from 'react';
export function AssemblyPlaybackControls({
  playing,
  disabled,
  position,
  total,
  muted,
  onToggle,
  onStep,
  onMute,
  onFullscreen,
  children,
}: {
  playing: boolean;
  disabled: boolean;
  position: number;
  total: number;
  muted: boolean;
  onToggle: () => void;
  onStep: (delta: number) => void;
  onMute: () => void;
  onFullscreen: () => void;
  children?: ReactNode;
}) {
  return (
    <div className="assembly-player-controls">
      <AssemblyAction label="上一帧" icon={<StepBack size={16} />} disabled={disabled} onClick={() => onStep(-1)} />
      <AssemblyAction
        label={playing ? '暂停' : '播放'}
        icon={playing ? <Pause size={17} /> : <Play size={17} />}
        type="primary"
        disabled={disabled}
        onClick={onToggle}
      />
      <AssemblyAction label="下一帧" icon={<StepForward size={16} />} disabled={disabled} onClick={() => onStep(1)} />
      <output aria-label="播放位置">
        {framecode(position)} <span>/ {framecode(total)}</span>
      </output>
      <div className="assembly-player-extra">
        {children}
        <AssemblyAction
          label={muted ? '取消预览静音' : '预览静音'}
          icon={muted ? <VolumeX size={16} /> : <Volume2 size={16} />}
          disabled={disabled}
          aria-pressed={muted}
          onClick={onMute}
        />
        <AssemblyAction label="全屏" icon={<Maximize size={16} />} onClick={onFullscreen} />
      </div>
    </div>
  );
}
