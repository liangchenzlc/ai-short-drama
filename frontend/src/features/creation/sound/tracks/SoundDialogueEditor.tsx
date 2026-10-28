import { ConfigSelect } from '@host/features/ai-config/selection/ModelSelect';
import { AssemblyAction } from '@host/features/creation/assembly/export/AssemblyAction';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import type { Dialogue } from '@host/types/creation/sound/tracks';
import { Button, Input, InputNumber, Tooltip } from 'antd';
import { Captions, Check, Mic, Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';
import './sound.css';

import type { SoundDialogueEditorProps } from '@host/types/creation/sound/editor';
export function SoundDialogueEditor({
  state,
  busy,
  disabled,
  inline,
  onEdit: edit,
  scope,
  selection,
  extractionState,
  speech,
  onSaveVoice,
}: SoundDialogueEditorProps) {
  const doc = state.document;
  const { selected, setSelected } = selection;
  const { extraction, extractId, onExtract, onAdoptExtraction } = extractionState;
  const { candidates, active, generate, resume, adopt } = speech;
  const line = doc.dialogue.find((item) => item.id === selected);
  const [textConfig, setTextConfig] = useState<string>();
  const [voiceCharacter, setVoiceCharacter] = useState(''),
    [voiceName, setVoiceName] = useState('');
  function editLine(change: Partial<Dialogue>) {
    if (line)
      edit({ ...doc, dialogue: doc.dialogue.map((item) => (item.id === line.id ? { ...item, ...change } : item)) });
  }
  const newLine = (): Dialogue => ({
    id: crypto.randomUUID(),
    character: '旁白',
    text: '',
    voice: '',
    config_id: null,
    start_ms: 0,
    media_id: null,
    adopted_hash: null,
  });

  return (
    <>
      <div className="sound-actions">
        <ConfigSelect
          kind="text"
          label="台词提取模型"
          optionLayout={inline ? 'compact' : undefined}
          presentation={inline ? 'standard-creation' : undefined}
          value={textConfig}
          onChange={setTextConfig}
          disabled={busy || disabled}
        />
        <Button
          className="assembly-icon-action"
          icon={<Captions size={16} />}
          aria-label="从已保存分镜提取台词"
          title="从已保存分镜提取台词"
          loading={busy || (!!extraction && ['queued', 'running'].includes(extraction.status))}
          disabled={busy || disabled || (!!extraction && ['queued', 'running'].includes(extraction.status))}
          onClick={() => void onExtract(textConfig)}
        />
        <AssemblyAction
          label="添加台词"
          icon={<Plus size={16} />}
          disabled={busy || disabled || doc.dialogue.length >= 300}
          onClick={() => {
            const l = newLine();
            edit({ ...doc, dialogue: [...doc.dialogue, l] });
            setSelected(l.id);
          }}
        />
      </div>
      {extraction && (
        <>
          <StudioFeedback
            type={extraction.error ? 'warning' : 'info'}
            noticeKey={`${scope}:extraction:${extractId}`}
            quiet={!extraction.error}
            persistent
            message={extraction.error || `台词提取：${extraction.status}`}
            description="提取文本需人工校对，建议时间不代表准确对齐。"
          />
          {(extraction.raw_text || extraction.dialogue) && (
            <div className="sound-extraction">
              <pre>{extraction.raw_text}</pre>
              {extraction.dialogue && (
                <Button disabled={busy || disabled} onClick={() => void onAdoptExtraction()}>
                  放入编辑区校对
                </Button>
              )}
            </div>
          )}
        </>
      )}
      <div className="sound-editor">
        <nav aria-label="台词列表">
          {doc.dialogue.map((l, i) => (
            <button type="button" key={l.id} aria-pressed={selected === l.id} onClick={() => setSelected(l.id)}>
              <strong>
                {i + 1}. {l.character}
              </strong>
              <span>{l.text || '待填写台词'}</span>
              <small>{l.media_id ? (state.stale_lines.includes(l.id) ? '配音已过期' : '已采用配音') : '待配音'}</small>
            </button>
          ))}
        </nav>
        {line ? (
          <section className="sound-fields">
            <label>
              角色
              <Input value={line.character} maxLength={100} onChange={(e) => editLine({ character: e.target.value })} />
            </label>
            <label>
              台词
              <Input.TextArea
                value={line.text}
                rows={4}
                maxLength={4096}
                onChange={(e) => editLine({ text: e.target.value })}
              />
            </label>
            <label>
              配音模型
              <ConfigSelect
                kind="audio"
                optionLayout={inline ? 'compact' : undefined}
                presentation={inline ? 'standard-creation' : undefined}
                value={line.config_id ?? undefined}
                disabled={busy || disabled}
                autoDefault={false}
                onChange={(v) => editLine({ config_id: v ?? null })}
              />
            </label>
            <label>
              音色标识
              <Input
                value={line.voice}
                maxLength={128}
                placeholder="填写配音服务提供的音色标识"
                onChange={(e) => editLine({ voice: e.target.value })}
              />
            </label>
            <Button
              disabled={busy || disabled || !state.voice_defaults.voices[line.character]}
              onClick={() =>
                editLine({
                  voice: state.voice_defaults.voices[line.character],
                })
              }
            >
              使用角色默认音色
            </Button>
            <label>
              开始时间（秒）
              <InputNumber
                min={0}
                max={3600}
                precision={3}
                step={0.001}
                value={line.start_ms / 1000}
                onChange={(v) =>
                  editLine({
                    start_ms: Math.round((v ?? 0) * 1000),
                  })
                }
              />
            </label>
            {line.media_id && state.media[line.media_id] && (
              <>
                <audio
                  controls
                  preload="metadata"
                  onError={() => speech.onAudioError()}
                  src={state.media[line.media_id].url}
                />
                <p>实际时长 {state.media[line.media_id].duration_ms} 毫秒</p>
                <Button
                  onClick={() =>
                    editLine({
                      media_id: null,
                      adopted_hash: null,
                    })
                  }
                >
                  取消采用
                </Button>
              </>
            )}
            <div className="sound-actions">
              <AssemblyAction
                type="primary"
                label="生成此条配音"
                icon={<Mic size={16} />}
                loading={busy || active}
                disabled={busy || disabled || active || !line.text.trim() || !line.voice.trim()}
                onClick={() => void generate()}
              />
              <AssemblyAction
                label="删除台词"
                icon={<Trash2 size={16} />}
                danger
                disabled={busy || disabled}
                onClick={() => {
                  edit({
                    ...doc,
                    dialogue: doc.dialogue.filter((l) => l.id !== line.id),
                  });
                  setSelected('');
                }}
              />
            </div>
            <h3>配音候选</h3>
            {!candidates.length && <p>还没有配音结果</p>}
            {candidates.map((c, i) => (
              <article key={c.id} className="sound-candidate">
                <p title={`任务 ${c.id}`}>
                  配音 {i + 1}
                  <span>
                    {(
                      {
                        queued: '排队中',
                        running: '生成中',
                        succeeded: '已完成',
                        failed: '失败',
                        cancelled: '已取消',
                      } as Record<string, string>
                    )[c.status] ?? c.status}
                  </span>
                </p>
                {(c.error || c.can_resume) && (
                  <StudioFeedback
                    type={c.error ? 'error' : 'info'}
                    noticeKey={`${scope}:candidate:${c.id}`}
                    quiet={!c.error}
                    persistent
                    message={c.error?.message || '配音结果可恢复保存'}
                    description={`配音任务 ${c.id}`}
                    actions={
                      c.can_resume && (
                        <Button disabled={busy || disabled} onClick={() => void resume(c.id)}>
                          恢复保存已有结果
                        </Button>
                      )
                    }
                  />
                )}
                {c.outputs.map((o) => (
                  <div className="sound-candidate-output" key={o.media_id}>
                    <audio controls preload="metadata" onError={() => speech.onAudioError()} src={o.url} />
                    <span>{(o.duration_ms / 1000).toFixed(2)} 秒</span>
                    <AssemblyAction
                      label="采用此配音"
                      icon={<Check size={16} />}
                      disabled={busy || disabled || speech.dirty || o.media_id === line.media_id}
                      onClick={() => void adopt(line.id, o.media_id)}
                    />
                  </div>
                ))}
              </article>
            ))}
          </section>
        ) : (
          <p>选择或添加一条台词，校对后生成配音。</p>
        )}
      </div>
      <details>
        <Tooltip title="默认音色只在手动应用时填入台词，不会覆盖已有配音。" trigger={['hover', 'focus']}>
          <summary>项目角色默认音色</summary>
        </Tooltip>
        <div className="sound-actions">
          <Input
            aria-label="默认音色角色"
            placeholder="角色名"
            value={voiceCharacter}
            onChange={(e) => setVoiceCharacter(e.target.value)}
          />
          <Input
            aria-label="默认音色标识"
            placeholder="音色标识"
            value={voiceName}
            onChange={(e) => setVoiceName(e.target.value)}
          />
          <Button
            disabled={busy || disabled || !voiceCharacter.trim() || !voiceName.trim()}
            onClick={() => void onSaveVoice(voiceCharacter.trim(), voiceName.trim())}
          >
            保存默认音色
          </Button>
        </div>
        {Object.entries(state.voice_defaults.voices).map(([name, voice]) => (
          <p key={name}>
            {name}：{voice}
          </p>
        ))}
      </details>
    </>
  );
}
