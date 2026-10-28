import { nativeVoiceApi as api } from '@host/api/creation/sound/voices';
import { generations } from '@host/api/generations/tasks/requests';
import { errorMessage } from '@host/api/http';
import { ConfigSelect } from '@host/features/ai-config/selection/ModelSelect';
import {
  clearRecovery,
  creationScope,
  isStoredDraft,
  readRecovery,
  readRecoveryText,
  saveRecovery,
} from '@host/features/creation/shared/recovery/creation-recovery';
import { releaseStandardSession, retainStandardSession } from '@host/features/creation/shared/sessions/registry';
import { ResourceEditingSession } from '@host/features/creation/shared/sessions/resource-editing-session';
import { useAuth } from '@host/features/identity/session/AuthSession';
import { attemptStorage, clearAttempt, requestAttempt } from '@host/features/shared/async/request-receipt';
import { confirmAction } from '@host/shared/ui/confirm';
import { Dialog } from '@host/shared/ui/Dialog';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import type { StoredDraft } from '@host/types/creation/shared/recovery/creation-recovery';
import type { CharacterVoice, NativeDialogue, SoundMode } from '@host/types/creation/sound/voices';
import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';
import { Button, Checkbox, Input, Select, Spin, Tooltip } from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import '../../assembly/workbench/standard-assembly.css';
import './native-voice.css';
function useNativeEnabled() {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    let gone = false;
    void api
      .enabled()
      .then((value) => {
        if (!gone) setEnabled(value);
      })
      .catch(() => {});
    return () => {
      gone = true;
    };
  }, []);
  return enabled;
}

export function NativeSoundMode({
  projectId,
  disabled,
  onChanged,
}: {
  projectId: string;
  disabled: boolean;
  onChanged: () => void;
}) {
  const enabled = useNativeEnabled();
  const [mode, setMode] = useState<SoundMode | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const lock = useRef(false);
  useEffect(() => {
    let gone = false;
    if (enabled)
      void api
        .mode(projectId)
        .then((v) => {
          if (!gone) setMode(v);
        })
        .catch((e) => {
          if (!gone) setError(errorMessage(e));
        });
    return () => {
      gone = true;
    };
  }, [projectId, enabled]);
  if (!enabled) return null;
  return (
    <div className="native-mode">
      <span>
        声音制作：
        {mode?.mode === 'native' ? '角色音色驱动视频' : '历史后期配音'}
      </span>
      <Button
        disabled={disabled || !mode}
        loading={busy}
        onClick={async () => {
          if (
            !mode ||
            lock.current ||
            !(await confirmAction(
              '切换制作模式会影响后续生成和导出。历史配音保留，新模式不叠加历史配音；请先保存分镜并在成片中重新核对声音。',
              { className: 'standard-creation-dialog' },
            ))
          )
            return;
          lock.current = true;
          setBusy(true);
          setError('');
          void api
            .setMode(projectId, {
              ...mode,
              mode: mode.mode === 'native' ? 'legacy' : 'native',
            })
            .then((v) => {
              setMode(v);
              onChanged();
            })
            .catch((e) => setError(errorMessage(e)))
            .finally(() => {
              lock.current = false;
              setBusy(false);
            });
        }}
      >
        切换制作模式
      </Button>
      {error && <StudioFeedback type="error" noticeKey={`sound-mode:${projectId}`} message={error} />}
    </div>
  );
}

export function CharacterVoicePanel({
  projectId,
  characterId,
  disabled,
}: {
  projectId: string;
  characterId: string;
  disabled: boolean;
}) {
  const enabled = useNativeEnabled(),
    scope = `character-voice:${projectId}:${characterId}`;
  const [state, setState] = useState<CharacterVoice | null>(null),
    [description, setDescription] = useState('');
  const [preview, setPreview] = useState('清晨的风吹过窗前，今天又是新的开始，我们一起出发吧。');
  const [config, setConfig] = useState<string>(),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const lock = useRef(false),
    alive = useRef(true);
  const [reload, setReload] = useState(0);
  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const result = await api.voices(projectId, characterId, signal);
      if (!signal?.aborted && alive.current) setState(result);
      return result;
    },
    [projectId, characterId],
  );
  const pending = !!state?.candidates.some((c) => ['queued', 'running'].includes(c.status));
  useEffect(() => {
    alive.current = true;
    if (!enabled) return;
    const controller = new AbortController();
    const timer = setTimeout(
      () => {
        void refresh(controller.signal)
          .catch((e) => {
            if (!controller.signal.aborted) setError(errorMessage(e));
          })
          .finally(() => {
            if (!controller.signal.aborted && pending) setReload((n) => n + 1);
          });
      },
      pending ? 3000 : 0,
    );
    return () => {
      alive.current = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, [enabled, refresh, pending, reload]);
  async function action(work: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError('');
    try {
      await work();
    } catch (e) {
      if (alive.current) setError(errorMessage(e));
    } finally {
      lock.current = false;
      if (alive.current) setBusy(false);
    }
  }
  const active = state?.candidates.some(
    (c) =>
      ['queued', 'running'].includes(c.status) ||
      c.can_resume ||
      ['provider_acceptance_unknown', 'acceptance_unknown', 'message_delivery_unknown'].includes(c.error?.code ?? ''),
  );
  if (!enabled) return null;
  return (
    <section className="native-voice" aria-label="角色声音">
      <Tooltip
        title="按声音描述设计音色，试听采用后，所有相关分镜复用这份样音。样音内容不会作为分镜台词。"
        trigger={['hover', 'focus']}
      >
        <h3 tabIndex={0}>角色声音</h3>
      </Tooltip>
      <Button
        disabled={busy || disabled}
        onClick={() =>
          void action(async () => {
            await refresh();
          })
        }
      >
        刷新声音候选
      </Button>
      {error && <StudioFeedback type="error" noticeKey={`${scope}:error`} message={error} />}
      {state?.current_voice && (
        <section aria-label="当前采用音色">
          <strong>当前角色音色</strong>
          <div className="native-voice-audio">
            {state.current_voice.url ? (
              <audio aria-label="试听当前角色音色" controls preload="none" src={state.current_voice.url} />
            ) : (
              <div className="native-voice-audio-empty" role="status">
                <span>音色地址暂不可用，请刷新后重试。</span>
                <Button
                  size="small"
                  disabled={busy || disabled}
                  onClick={() =>
                    void action(async () => {
                      await refresh();
                    })
                  }
                >
                  刷新角色音色
                </Button>
              </div>
            )}
          </div>
          {state.current_voice.duration_ms != null && (
            <p>样音 {(state.current_voice.duration_ms / 1000).toFixed(2)} 秒</p>
          )}
        </section>
      )}
      <fieldset disabled={disabled || busy}>
        <ConfigSelect
          kind="audio"
          label="百炼音色设计模型"
          optionLayout="detailed"
          presentation="standard-creation"
          value={config}
          onChange={setConfig}
          onResolvedChange={setConfig}
          disabled={disabled || busy}
        />
        <label>
          声音描述
          <Input.TextArea
            aria-label="声音描述"
            maxLength={500}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="例如：青年男性，中低音，略带沙哑，语速平稳"
          />
        </label>
        <Tooltip
          title="15～200 字符；建议简短自然，生成后须为 3～7.5 秒才可用于视频参考。"
          trigger={['hover', 'focus']}
        >
          <label>
            试音文本
            <Input.TextArea
              aria-label="试音文本"
              maxLength={200}
              value={preview}
              onChange={(e) => setPreview(e.target.value)}
            />
          </label>
        </Tooltip>
        <Button
          disabled={
            disabled || busy || active || !state || !config || !description.trim() || preview.trim().length < 15
          }
          loading={busy}
          onClick={() =>
            void action(async () => {
              if (!config) return;
              const body = { config, description, preview };
              const key = await requestAttempt(scope, body, attemptStorage());
              await api.design(projectId, characterId, config, description, preview, key);
              clearAttempt(scope, attemptStorage());
              await refresh();
            })
          }
        >
          设计一个音色候选
        </Button>
      </fieldset>
      {!state ? (
        <Spin />
      ) : !state.candidates.length ? (
        <p>还没有音色候选。填写描述，生成后试听选择。</p>
      ) : (
        <ul className="native-candidates">
          {state.candidates.map((c) => (
            <li key={c.record_id}>
              <strong>
                {state.record_id === c.record_id
                  ? '当前角色音色'
                  : c.status === 'succeeded'
                    ? '待试听候选'
                    : ['queued', 'running'].includes(c.status)
                      ? '正在设计与保存'
                      : '任务未完成'}
              </strong>
              <p>{c.description}</p>
              {c.url && <audio aria-label="试听角色音色" controls preload="none" src={c.url} />}
              {c.duration_ms != null && (
                <Tooltip
                  title={!c.adoptable ? '不满足参考要求，请调整试音文本后重新设计。' : undefined}
                  trigger={['hover', 'focus']}
                >
                  <p tabIndex={!c.adoptable ? 0 : undefined}>
                    样音 {(c.duration_ms / 1000).toFixed(2)} 秒{!c.adoptable && ' · 需调整'}
                  </p>
                </Tooltip>
              )}
              {c.error && (
                <StudioFeedback
                  type="warning"
                  noticeKey={`${scope}:candidate:${c.record_id}`}
                  message={c.error.message}
                />
              )}
              <div className="native-actions">
                {c.adoptable && (
                  <Button
                    disabled={disabled || busy || state.record_id === c.record_id}
                    onClick={() =>
                      void action(async () =>
                        setState(await api.adopt(projectId, characterId, state.row_version, c.record_id)),
                      )
                    }
                  >
                    设为角色音色
                  </Button>
                )}
                {c.can_resume && (
                  <Button
                    disabled={disabled || busy}
                    onClick={() =>
                      void action(async () => {
                        await generations.resume(c.task_id);
                        await refresh();
                      })
                    }
                  >
                    恢复已有样音保存
                  </Button>
                )}
                {['queued', 'running'].includes(c.status) && (
                  <Button
                    disabled={disabled || busy}
                    onClick={() =>
                      void action(async () => {
                        await generations.cancel(c.task_id);
                        await refresh();
                      })
                    }
                  >
                    停止等待
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function NativeDialoguePanel({
  projectId,
  shotId,
  disabled,
  prepare,
  onChanged,
  revision,
  registerBarrier,
}: {
  projectId: string;
  shotId: string;
  disabled: boolean;
  prepare: () => Promise<unknown>;
  onChanged: () => void;
  revision?: number;
  registerBarrier?: (barrier: NavigationBarrier | null) => void;
}) {
  const enabled = useNativeEnabled();
  const { user } = useAuth();
  const account = user?.id ?? 'anonymous',
    scope = `native-dialogue:${projectId}:${shotId}`;
  const session = useMemo(
    () =>
      retainStandardSession(
        account,
        scope,
        () => {
          const recoveryScope = creationScope(scope, account);
          return new ResourceEditingSession<NativeDialogue, NativeDialogue['document']>({
            read: () => api.dialogue(projectId, shotId),
            save: async (base, document, assertActive) => {
              const body = { row_version: base.row_version, document };
              const request_id = await requestAttempt(scope, body, attemptStorage());
              assertActive();
              const saved = await api.saveDialogue(projectId, shotId, {
                ...body,
                request_id,
              });
              assertActive();
              clearAttempt(scope, attemptStorage());
              return saved;
            },
            version: (value) => String(value.row_version),
            document: (value) => value.document,
            apply: (value, document) => ({ ...value, document }),
            recovery: {
              read: () =>
                readRecovery(
                  recoveryScope,
                  (value): value is StoredDraft<NativeDialogue['document']> =>
                    isStoredDraft(value) &&
                    !!value.document &&
                    typeof value.document === 'object' &&
                    Array.isArray((value.document as NativeDialogue['document']).lines) &&
                    typeof (value.document as NativeDialogue['document']).reviewed === 'boolean',
                ),
              save: (value) => saveRecovery(recoveryScope, value),
              clear: () => clearRecovery(recoveryScope),
              export: () => readRecoveryText(recoveryScope),
              previous: () => readRecoveryText(`${recoveryScope}:previous`),
              backup: (raw) => localStorage.setItem(`${recoveryScope}:previous`, raw),
            },
          });
        },
        { label: '分镜对白', projectId },
      ),
    [account, scope, projectId, shotId],
  );
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const state = snapshot.value,
    doc = state?.document ?? null;
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const lock = useRef(false);
  useEffect(() => {
    if (enabled) void session.load();
  }, [enabled, session, revision]);
  useEffect(() => () => releaseStandardSession(account, scope), [account, scope]);
  useEffect(() => {
    registerBarrier?.({
      hasUnsettled: session.hasUnsettled,
      flush: session.flush,
    });
    return () => registerBarrier?.(null);
  }, [session, registerBarrier]);
  if (!enabled || state?.mode === 'legacy') return null;
  const edit = (next: NativeDialogue['document']) => session.edit({ ...next, reviewed: false });
  async function save() {
    if (!state || !doc || lock.current || disabled) return;
    lock.current = true;
    setBusy(true);
    setError('');
    try {
      if (!(await prepare())) return;
      if (await (session.getSnapshot().status === 'error' ? session.retry() : session.flush())) {
        setOpen(false);
        onChanged();
      }
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  async function chooseVersion(keepLocal: boolean) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError('');
    try {
      await session.reload(keepLocal);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  function downloadRecovery() {
    try {
      const raw = session.exportRecovered();
      if (!raw) return;
      const url = URL.createObjectURL(new Blob([raw], { type: 'application/json;charset=utf-8' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `dialogue-${shotId}-recovery.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }
  const versionActions =
    snapshot.status === 'conflict' || snapshot.recoveryBlocked ? (
      <div className="native-actions">
        <Button disabled={busy} onClick={() => void chooseVersion(true)}>
          以当前版本保留本机修改
        </Button>
        <Button disabled={busy} onClick={() => void chooseVersion(false)}>
          使用服务端版本
        </Button>
      </div>
    ) : (
      <Button disabled={busy} onClick={() => void session.retry()}>
        重试保存
      </Button>
    );
  return (
    <section className="native-dialogue" aria-label="分镜原生对白">
      <h4>原生对白</h4>
      <Tooltip
        title={
          state?.document.reviewed
            ? `${state.document.lines.length} 句已确认对白`
            : '生成视频前，请确认角色、台词与音色；无对白镜头也需确认。'
        }
      >
        <span className="creation-help" tabIndex={0}>
          {state?.document.reviewed ? '对白已确认' : '对白待确认'}
        </span>
      </Tooltip>
      {state?.document.lines.map((line, i) => (
        <p key={i}>
          {i + 1}. {state.characters.find((c) => c.id === line.character_id)?.name ?? '角色已移除'}：{line.text}
        </p>
      ))}
      {state?.voices.map((v) => (
        <div key={v.character_id}>
          <span>
            {v.name} · {v.url ? `音色版本 ${v.version}` : '未绑定音色，请到角色库设计'}
          </span>
          {v.url && <audio aria-label={`${v.name}参考音色`} controls preload="none" src={v.url} />}
        </div>
      ))}
      <Button disabled={disabled || !state} onClick={() => setOpen(true)}>
        编辑并确认对白
      </Button>
      {session.hasPrevious() && (
        <Button type="text" onClick={downloadRecovery}>
          下载本机备份
        </Button>
      )}
      {(error || snapshot.message) && !open && (
        <StudioFeedback
          type={snapshot.status === 'conflict' ? 'warning' : 'error'}
          message={error || snapshot.message}
          action={versionActions}
        />
      )}
      {open && doc && state && (
        <Dialog
          className="standard-creation-dialog native-dialogue-dialog"
          title="分镜对白与声音表演"
          canClose={!busy}
          onClose={() => {
            setOpen(false);
            void session.flush();
          }}
        >
          <div className="native-voice">
            <p>最多两位角色按列表顺序轮流说话。角色外观沿用分镜参考图，声音使用角色库中已确认的样音。</p>
            {(error || snapshot.message) && (
              <StudioFeedback
                type={snapshot.status === 'conflict' ? 'warning' : 'error'}
                message={error || snapshot.message}
                action={versionActions}
              />
            )}
            <fieldset disabled={disabled || busy}>
              {doc.lines.map((line, i) => (
                <div className="native-line" key={i}>
                  <label>
                    说话角色
                    <Select
                      classNames={{
                        popup: { root: 'standard-creation-popup' },
                      }}
                      getPopupContainer={(trigger) =>
                        trigger.closest('dialog') ?? trigger.parentElement ?? document.body
                      }
                      aria-label={`第${i + 1}句角色`}
                      value={line.character_id || undefined}
                      options={state.characters.map((c) => ({
                        value: c.id,
                        label: c.name,
                      }))}
                      onChange={(v) =>
                        edit({
                          ...doc,
                          lines: doc.lines.map((l, n) => (n === i ? { ...l, character_id: v } : l)),
                        })
                      }
                    />
                  </label>
                  <label>
                    台词
                    <Input.TextArea
                      aria-label={`第${i + 1}句台词`}
                      value={line.text}
                      maxLength={500}
                      onChange={(e) =>
                        edit({
                          ...doc,
                          lines: doc.lines.map((l, n) => (n === i ? { ...l, text: e.target.value } : l)),
                        })
                      }
                    />
                  </label>
                  <label>
                    语气
                    <Input
                      value={line.delivery}
                      maxLength={200}
                      onChange={(e) =>
                        edit({
                          ...doc,
                          lines: doc.lines.map((l, n) => (n === i ? { ...l, delivery: e.target.value } : l)),
                        })
                      }
                    />
                  </label>
                  <label>
                    说话方式
                    <Select
                      classNames={{
                        popup: { root: 'standard-creation-popup' },
                      }}
                      getPopupContainer={(trigger) =>
                        trigger.closest('dialog') ?? trigger.parentElement ?? document.body
                      }
                      value={line.speech}
                      options={[
                        { value: 'onscreen', label: '画内说话，同步口型' },
                        { value: 'voiceover', label: '画外音' },
                      ]}
                      onChange={(v) =>
                        edit({
                          ...doc,
                          lines: doc.lines.map((l, n) => (n === i ? { ...l, speech: v } : l)),
                        })
                      }
                    />
                  </label>
                  <div className="native-actions">
                    <Button
                      disabled={i === 0}
                      onClick={() => {
                        const lines = [...doc.lines];
                        [lines[i - 1], lines[i]] = [lines[i], lines[i - 1]];
                        edit({ ...doc, lines });
                      }}
                    >
                      上移
                    </Button>
                    <Button
                      danger
                      onClick={() =>
                        edit({
                          ...doc,
                          lines: doc.lines.filter((_, n) => n !== i),
                        })
                      }
                    >
                      删除台词
                    </Button>
                  </div>
                </div>
              ))}
              <Button
                disabled={!state.characters.length || doc.lines.length >= 20}
                onClick={() =>
                  edit({
                    ...doc,
                    lines: [
                      ...doc.lines,
                      {
                        character_id: state.characters[0].id,
                        text: '',
                        delivery: '',
                        speech: 'onscreen',
                      },
                    ],
                  })
                }
              >
                添加台词
              </Button>
              {!state.characters.length && <p>当前分镜没有关联角色；请先关联素材，或确认这是无对白镜头。</p>}
              <p>台词需适合视频时长；内容过长可能说不完，请手动缩短台词或调整分镜。</p>
            </fieldset>
          </div>
          <footer className="native-dialogue-footer">
            <Checkbox
              disabled={disabled || busy}
              checked={doc.reviewed}
              onChange={(e) => session.edit({ ...doc, reviewed: e.target.checked })}
            >
              已确认对白顺序与角色；空列表表示无对白
            </Checkbox>
            <Button type="primary" disabled={disabled} loading={busy} onClick={() => void save()}>
              保存分镜对白
            </Button>
          </footer>
        </Dialog>
      )}
    </section>
  );
}
