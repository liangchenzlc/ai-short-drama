import { AssemblyAction } from '@host/features/creation/assembly/export/AssemblyAction';
import {
  anchorAt,
  clipSpans
} from '@host/features/creation/assembly/sessions/assembly-document';
import { AudioWaveform } from '@host/features/creation/assembly/timeline/AssemblyMultitrack';
import type { AudioInspectorProps } from '@host/types/creation/assembly/inspector/panels';
import { Button, Checkbox, Input, InputNumber, Select, Slider } from 'antd';
import {
  Plus,
  RotateCcw,
  Trash2,
  WandSparkles
} from 'lucide-react';

export function AudioInspector({
  disabled,
  onMaterial,
  audio,
  onSelectAux,
  doc,
  isTrackLocked,
  audioChange,
  linkControl,
  change,
  analysis,
  editor,
  clips,
  busy,
  normalizeLoudness
}: AudioInspectorProps) {
  return (<div className="assembly-inspector-fields">
    <div className="assembly-inspector-row">
      <Button
        size="small"
        icon={<Plus size={14} />}
        disabled={disabled}
        onClick={() => onMaterial(true, false, 'music')}
      >
        配乐
      </Button>
      <Button
        size="small"
        icon={<Plus size={14} />}
        disabled={disabled}
        onClick={() => onMaterial(true, false, 'effect')}
      >
        音效
      </Button>
      <Button
        size="small"
        icon={<Plus size={14} />}
        disabled={disabled}
        onClick={() => onMaterial(true, false, 'dialogue')}
      >
        对白
      </Button>
    </div>
    <Select
      aria-label="选择声音片段"
      value={audio?.id}
      placeholder="选择声音片段"
      onChange={onSelectAux}
      options={doc.audio_clips.map((c) => ({
        value: c.id,
        label: c.name,
      }))}
    />
    {audio ? (
      <fieldset disabled={isTrackLocked(audio.kind)} className="assembly-inspector-fields">
        <div className="assembly-inspector-row">
          <Input
            aria-label="声音名称"
            value={audio.name}
            maxLength={255}
            onChange={(event) => audioChange({ name: event.target.value })}
          />
          {linkControl}
          <AssemblyAction
            label="删除声音"
            icon={<Trash2 size={15} />}
            onClick={() => {
              change({
                ...doc,
                audio_clips: doc.audio_clips.filter((c) => c.id !== audio.id),
              });
              onSelectAux('');
            }}
          />
        </div>
        <div className="assembly-waveform-preview">
          <AudioWaveform
            values={analysis.waveforms[audio.media_id]}
            clip={audio}
            duration={editor.value?.sound?.media[audio.media_id]?.duration_ms}
          />
        </div>
        <label>
          开始（秒）
          <InputNumber
            aria-label="声音开始时间"
            min={0}
            max={3600}
            step={0.1}
            value={audio.start_ms / 1000}
            onChange={(value) =>
              value !== null &&
              audioChange({
                start_ms: Math.round(value * 1000),
                ...(audio.anchor_clip_id ? anchorAt(clips, Math.round(value * 1000)) : {}),
              })
            }
          />
        </label>
        <div className="assembly-inspector-pair">
          <label>
            裁剪起点
            <InputNumber
              aria-label="声音裁剪起点"
              min={0}
              step={0.1}
              value={audio.trim_in_ms / 1000}
              onChange={(value) =>
                value !== null &&
                audioChange({
                  trim_in_ms: Math.round(value * 1000),
                  duration_ms: audio.loop
                    ? audio.duration_ms
                    : audio.trim_out_ms - Math.round(value * 1000),
                })
              }
            />
          </label>
          <label>
            裁剪终点
            <InputNumber
              aria-label="声音裁剪终点"
              min={0.001}
              step={0.1}
              value={audio.trim_out_ms / 1000}
              onChange={(value) =>
                value !== null &&
                audioChange({
                  trim_out_ms: Math.round(value * 1000),
                  duration_ms: audio.loop ? audio.duration_ms : Math.round(value * 1000) - audio.trim_in_ms,
                })
              }
            />
          </label>
        </div>
        <label>
          音量
          <Slider
            aria-label="声音音量"
            min={0}
            max={2}
            step={0.01}
            value={audio.volume}
            onChange={(volume) => audioChange({ volume })}
          />
        </label>
        <div className="assembly-inspector-pair">
          <label>
            淡入（秒）
            <InputNumber
              aria-label="声音淡入"
              min={0}
              max={audio.duration_ms / 1000}
              step={0.1}
              value={audio.fade_in_ms / 1000}
              onChange={(v) => v !== null && audioChange({ fade_in_ms: Math.round(v * 1000) })}
            />
          </label>
          <label>
            淡出（秒）
            <InputNumber
              aria-label="声音淡出"
              min={0}
              max={audio.duration_ms / 1000}
              step={0.1}
              value={audio.fade_out_ms / 1000}
              onChange={(v) => v !== null && audioChange({ fade_out_ms: Math.round(v * 1000) })}
            />
          </label>
        </div>
        <div className="assembly-inspector-row">
          <Checkbox checked={audio.muted} onChange={(e) => audioChange({ muted: e.target.checked })}>
            静音
          </Checkbox>
          <Checkbox
            checked={audio.loop}
            onChange={(e) =>
              audioChange({
                loop: e.target.checked,
                duration_ms: e.target.checked
                  ? Math.max(audio.duration_ms, (clipSpans(clips).at(-1)?.end ?? 0) - audio.start_ms)
                  : audio.trim_out_ms - audio.trim_in_ms,
              })
            }
          >
            循环
          </Checkbox>
        </div>
        {audio.loop && (
          <label>
            循环时长（秒）
            <InputNumber
              aria-label="声音循环时长"
              min={0.001}
              max={(3600000 - audio.start_ms) / 1000}
              value={audio.duration_ms / 1000}
              onChange={(v) => v !== null && audioChange({ duration_ms: Math.round(v * 1000) })}
            />
          </label>
        )}
        {audio.kind === 'music' && (
          <Checkbox
            checked={audio.ducking}
            onChange={(event) => audioChange({ ducking: event.target.checked })}
          >
            人声时自动压低配乐
          </Checkbox>
        )}
        <div className="assembly-inspector-row">
          <Button
            size="small"
            icon={<WandSparkles size={14} />}
            loading={busy}
            onClick={() => {
              void normalizeLoudness();
            }}
          >
            统一响度
          </Button>
          <span>{audio.gain_db.toFixed(1)} dB</span>
          <AssemblyAction
            label="重置响度增益"
            icon={<RotateCcw size={14} />}
            onClick={() => audioChange({ gain_db: 0 })}
          />
        </div>
      </fieldset>
    ) : (
      <div className="assembly-material-empty">选择声音轨中的片段以调整</div>
    )}
  </div>);
}
