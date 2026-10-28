import { AssemblyAction } from '@host/features/creation/assembly/export/AssemblyAction';
import { Button, Checkbox, Dropdown, Input, InputNumber } from 'antd';
import { Ellipsis, Plus, Trash2 } from 'lucide-react';
import { useRef } from 'react';
import './sound.css';

import { SoundHelp } from '@host/features/creation/sound/tracks/SoundControls';
import type { SoundSubtitlesEditorProps } from '@host/types/creation/sound/editor';
export function SoundSubtitlesEditor({
  state,
  busy,
  disabled,
  inline,
  onEdit: edit,
  onImport,
  onFill,
  srtUrl,
  onNotice: setNotice,
}: SoundSubtitlesEditorProps) {
  const doc = state.document;
  const srtUpload = useRef<HTMLInputElement>(null);
  return (
    <>
      <div className="sound-actions">
        <AssemblyAction
          label="添加字幕"
          icon={<Plus size={16} />}
          disabled={busy || disabled || doc.subtitles.length >= 2000}
          onClick={() =>
            edit({
              ...doc,
              subtitles: [
                ...doc.subtitles,
                {
                  start_ms: doc.subtitles.at(-1)?.end_ms ?? 0,
                  end_ms: (doc.subtitles.at(-1)?.end_ms ?? 0) + 1000,
                  text: '',
                },
              ],
            })
          }
        />
        <label className="sound-file" hidden={inline}>
          导入 UTF-8 SRT
          <input
            ref={srtUpload}
            type="file"
            accept=".srt"
            aria-label="导入 UTF-8 SRT"
            disabled={busy || disabled}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = '';
              if (!file) return;
              if (file.size > 2 * 1024 ** 2) {
                setNotice('SRT 文件不能超过 2 MiB。');
                return;
              }
              void onImport(file);
            }}
          />
        </label>
        {inline ? (
          <Dropdown
            trigger={['click']}
            overlayClassName="standard-creation-menu standard-creation-popup"
            menu={{
              items: [
                {
                  key: 'import',
                  label: '导入 UTF-8 SRT',
                  disabled: busy || disabled,
                  onClick: () => srtUpload.current?.click(),
                },
                {
                  key: 'export',
                  label: (
                    <a href={srtUrl} download>
                      导出已保存 SRT
                    </a>
                  ),
                },
                ...(state.mode !== 'native'
                  ? [
                      {
                        key: 'fill',
                        label: '从采用的配音填入字幕',
                        disabled: busy || disabled || !doc.dialogue.length,
                        onClick: () => void onFill(),
                      },
                    ]
                  : []),
              ],
            }}
          >
            <Button
              type="text"
              className="assembly-icon-action"
              icon={<Ellipsis size={16} />}
              aria-label="字幕操作"
              title="字幕操作"
            />
          </Dropdown>
        ) : (
          <>
            <a href={srtUrl} download>
              导出已保存 SRT
            </a>
            {state.mode !== 'native' && (
              <Button disabled={busy || disabled || !doc.dialogue.length} onClick={() => void onFill()}>
                从采用的配音填入字幕
              </Button>
            )}
          </>
        )}
      </div>
      <Checkbox
        checked={doc.burn_subtitles}
        disabled={busy || disabled}
        onChange={(e) => edit({ ...doc, burn_subtitles: e.target.checked })}
      >
        导出时烧录字幕
      </Checkbox>
      <SoundHelp
        label="字幕时间说明"
        title="字幕按开始时间排列且不能重叠。配音可能有停顿，请人工调整每条字幕的起止时间。"
      />
      <label>
        字幕字号
        <InputNumber
          min={12}
          max={72}
          precision={0}
          value={doc.font_size}
          onChange={(v) => edit({ ...doc, font_size: v ?? 24 })}
        />
      </label>
      <div className="sound-subtitle-list">
        {doc.subtitles.map((s, i) => (
          <div className="sound-subtitle" key={i}>
            <span className="sound-subtitle-number">{i + 1}</span>
            <label>
              开始（秒）
              <InputNumber
                min={0}
                max={3600}
                precision={3}
                step={0.001}
                value={s.start_ms / 1000}
                onChange={(v) =>
                  edit({
                    ...doc,
                    subtitles: doc.subtitles.map((x, j) =>
                      j === i
                        ? {
                            ...x,
                            start_ms: Math.round((v ?? 0) * 1000),
                          }
                        : x,
                    ),
                  })
                }
              />
            </label>
            <label>
              结束（秒）
              <InputNumber
                min={0.001}
                max={3600}
                precision={3}
                step={0.001}
                value={s.end_ms / 1000}
                onChange={(v) =>
                  edit({
                    ...doc,
                    subtitles: doc.subtitles.map((x, j) =>
                      j === i
                        ? {
                            ...x,
                            end_ms: Math.round((v ?? 0.001) * 1000),
                          }
                        : x,
                    ),
                  })
                }
              />
            </label>
            <label className="sound-subtitle-text">
              <span className="sound-field-label">字幕文本</span>
              <Input.TextArea
                aria-label="字幕文本"
                autoSize={{ minRows: 1, maxRows: 4 }}
                value={s.text}
                maxLength={1000}
                onChange={(e) =>
                  edit({
                    ...doc,
                    subtitles: doc.subtitles.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)),
                  })
                }
              />
            </label>
            <AssemblyAction
              label={`删除字幕 ${i + 1}`}
              icon={<Trash2 size={15} />}
              danger
              disabled={busy || disabled}
              onClick={() =>
                edit({
                  ...doc,
                  subtitles: doc.subtitles.filter((_, j) => i !== j),
                })
              }
            />
          </div>
        ))}
      </div>
    </>
  );
}
