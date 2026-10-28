import { AssemblyAction } from '@host/features/creation/assembly/export/AssemblyAction';
import { Checkbox, InputNumber, Select } from 'antd';
import { Trash2, Upload } from 'lucide-react';
import { useRef } from 'react';
import './sound.css';

import { SoundHelp, SoundVolume } from '@host/features/creation/sound/tracks/SoundControls';
import type { SoundMixingEditorProps } from '@host/types/creation/sound/editor';
export function SoundMixingEditor({
  state,
  busy,
  disabled,
  inline,
  onEdit: edit,
  onMetadata: setState,
  onUpload,
  onNotice: setNotice,
  onAudioError: setAudioError,
}: SoundMixingEditorProps) {
  const doc = state.document;
  const musicUpload = useRef<HTMLInputElement>(null);
  return (
    <div className="sound-fields">
      {!!state.uploads?.length && (
        <label>
          选择本集已上传配乐
          <Select
            classNames={inline ? { popup: { root: 'standard-creation-popup' } } : undefined}
            aria-label="选择本集已上传配乐"
            disabled={busy || disabled}
            value={doc.music?.media_id}
            options={state.uploads.map((m) => ({
              value: m.media_id,
              label: m.name,
            }))}
            onChange={(id) => {
              const m = state.uploads.find((v) => v.media_id === id)!;
              edit({
                ...doc,
                music: {
                  media_id: id,
                  start_ms: 0,
                  trim_in_ms: 0,
                  trim_out_ms: m.duration_ms,
                  volume: 0.2,
                  loop: false,
                  fade_in_ms: 500,
                  fade_out_ms: 500,
                  ducking: true,
                },
              });
              setState({
                ...state,
                media: { ...state.media, [id]: m },
              });
            }}
          />
        </label>
      )}
      <div className="sound-music-upload">
        <AssemblyAction
          label={doc.music ? '替换配乐' : '上传一条配乐'}
          icon={<Upload size={16} />}
          disabled={busy || disabled}
          loading={busy}
          onClick={() => musicUpload.current?.click()}
        />
        <span>
          {state.uploads.find((m) => m.media_id === doc.music?.media_id)?.name ?? (doc.music ? '本集配乐' : '添加配乐')}
        </span>
        <SoundHelp label="配乐文件要求" title="支持 MP3 / WAV / M4A，最多 100 MiB / 60 分钟。" />
        {doc.music && (
          <AssemblyAction
            label="移除配乐"
            icon={<Trash2 size={16} />}
            disabled={busy || disabled}
            onClick={() => edit({ ...doc, music: null })}
          />
        )}
      </div>
      <input
        ref={musicUpload}
        type="file"
        hidden
        accept=".mp3,.wav,.m4a"
        disabled={busy || disabled}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (!file) return;
          if (file.size > 100 * 1024 ** 2) {
            setNotice('配乐文件不能超过 100 MiB。');
            return;
          }
          void onUpload(file);
        }}
      />
      {doc.music && (
        <>
          <audio
            controls
            preload="metadata"
            onError={() => setAudioError('试听地址无法播放，请刷新地址后重试，当前编辑已保留。')}
            src={state.media[doc.music.media_id]?.url}
          />
          <SoundVolume
            label="配乐音量"
            disabled={busy || disabled}
            value={doc.music.volume}
            onChange={(value) =>
              edit({
                ...doc,
                music: { ...doc.music!, volume: value },
              })
            }
          />
          <Checkbox
            checked={doc.music.loop}
            disabled={busy || disabled}
            onChange={(e) =>
              edit({
                ...doc,
                music: { ...doc.music!, loop: e.target.checked },
              })
            }
          >
            循环配乐
          </Checkbox>
          <Checkbox
            checked={doc.music.ducking}
            disabled={busy || disabled}
            onChange={(e) =>
              edit({
                ...doc,
                music: {
                  ...doc.music!,
                  ducking: e.target.checked,
                },
              })
            }
          >
            对白期间自动降低配乐音量
          </Checkbox>
          <details className="sound-music-advanced">
            <summary>时间与淡入淡出</summary>
            <div className="sound-grid">
              {(['start_ms', 'trim_in_ms', 'trim_out_ms', 'fade_in_ms', 'fade_out_ms'] as const).map((k) => (
                <label key={k}>
                  {
                    {
                      start_ms: '开始（秒）',
                      trim_in_ms: '裁剪起点（秒）',
                      trim_out_ms: '裁剪终点（秒）',
                      fade_in_ms: '淡入（秒）',
                      fade_out_ms: '淡出（秒）',
                    }[k]
                  }
                  <InputNumber
                    min={0}
                    max={3600}
                    precision={3}
                    step={0.001}
                    value={doc.music![k] / 1000}
                    onChange={(v) =>
                      edit({
                        ...doc,
                        music: {
                          ...doc.music!,
                          [k]: Math.round((v ?? 0) * 1000),
                        },
                      })
                    }
                  />
                </label>
              ))}
            </div>
          </details>
        </>
      )}
      <SoundVolume
        label="原视频声音音量"
        disabled={busy || disabled}
        value={doc.original_volume}
        onChange={(value) => edit({ ...doc, original_volume: value })}
      />
      {state.mode !== 'native' && (
        <SoundVolume
          label="配音音量"
          disabled={busy || disabled}
          value={doc.dialogue_volume}
          onChange={(value) => edit({ ...doc, dialogue_volume: value })}
        />
      )}
    </div>
  );
}
