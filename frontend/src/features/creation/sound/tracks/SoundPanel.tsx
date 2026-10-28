import { soundApi } from '@host/api/creation/sound/tracks';
import { generations } from '@host/api/generations/tasks/requests';
import { errorMessage } from '@host/api/http';
import { AssemblyAction } from '@host/features/creation/assembly/export/AssemblyAction';
import {
  CreationRecoveryError,
  readRecoveryText,
  saveRecovery,
} from '@host/features/creation/shared/recovery/creation-recovery';
import { SoundHelp } from '@host/features/creation/sound/tracks/SoundControls';
import { SoundDialogueEditor } from '@host/features/creation/sound/tracks/SoundDialogueEditor';
import { SoundMixingEditor } from '@host/features/creation/sound/tracks/SoundMixingEditor';
import { SoundSubtitlesEditor } from '@host/features/creation/sound/tracks/SoundSubtitlesEditor';
import { useSoundSession } from '@host/features/creation/sound/tracks/useSoundSession';
import { useCreationWorkspace } from '@host/features/creation/workspace/layout/CreationWorkspace';
import { attemptStorage, clearAttempt, requestAttempt } from '@host/features/shared/async/request-receipt';
import { confirmAction } from '@host/shared/ui/confirm';
import { StudioFeedback, StudioFeedbackIndicator, StudioMessageProvider } from '@host/shared/ui/StudioFeedback';
import type { SoundPanelProps } from '@host/types/creation/sound/editor';
import type { AudioCandidate, Extraction, SoundDocument, SoundState } from '@host/types/creation/sound/tracks';
import { Button, Checkbox, ConfigProvider, Drawer, Dropdown, InputNumber, Spin, Tabs, Tooltip } from 'antd';
import { Captions, Download, Ellipsis, Save, Volume2, X } from 'lucide-react';
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { useSearchParams } from 'react-router-dom';
import './sound.css';
export function SoundPanel({
  projectId,
  disabled,
  readOnly = disabled,
  flushVideo,
  onSaved,
  registerBarrier,
  renderToolbar,
  panelContainer,
  onOpenChange,
}: SoundPanelProps) {
  const inline = useCreationWorkspace();
  const panelId = useId();
  const railSurface = useRef<HTMLElement>(null);
  const drawerSurface = useRef<HTMLDivElement>(null);
  const api = useMemo(() => soundApi(projectId), [projectId]);
  const [enabled, setEnabled] = useState(false),
    [open, setOpen] = useState(false);
  useLayoutEffect(() => {
    onOpenChange?.(inline && open);
    return () => onOpenChange?.(false);
  }, [inline, open, onOpenChange]);
  const [capabilityStatus, setCapabilityStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [capabilityError, setCapabilityError] = useState('');
  const [audioError, setAudioError] = useState('');
  const { session, recoveryScope } = useSoundSession(projectId, api);
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const state = snapshot.value,
    doc = state?.document ?? null,
    reviewed = !!state && !state.needs_review;
  const recoveredDraft = snapshot.recovered;
  const recoveryError = snapshot.recoveryBlocked ? snapshot.message : '';
  const [busy, setBusy] = useState(false),
    [notice, setNotice] = useState('');
  const [selected, setSelected] = useState('');
  const [searchParams] = useSearchParams();
  const requestedLine = searchParams.get('source_line');
  const locatedSource = useRef('');
  function setReviewed(value: boolean) {
    const current = session.getSnapshot().value;
    if (current)
      session.edit({
        document: current.document,
        reviewed: value,
        timeline_hash: current.timeline_hash,
      });
  }
  function setDoc(value: SoundDocument) {
    const current = session.getSnapshot().value;
    if (current)
      session.edit({
        document: value,
        reviewed: false,
        timeline_hash: current.timeline_hash,
      });
  }
  function setState(value: SoundState | ((previous: SoundState | null) => SoundState | null)) {
    session.updateMetadata((current) => {
      const next = typeof value === 'function' ? value(current) : value;
      return next
        ? {
            ...current,
            media: next.media,
            uploads: next.uploads,
            voice_defaults: next.voice_defaults,
          }
        : current;
    });
  }

  const [candidates, setCandidates] = useState<AudioCandidate[]>([]),
    [pollVersion, setPollVersion] = useState(0);
  const [extractId, setExtractId] = useState(''),
    [extraction, setExtraction] = useState<Extraction | null>(null);
  const lock = useRef(false),
    mounted = useRef(true);
  const entry = useRef<HTMLButtonElement | null>(null);
  const restoreFocus = useRef(false);
  useLayoutEffect(() => {
    if (inline && !open && restoreFocus.current) {
      const frame = requestAnimationFrame(() => {
        entry.current?.focus();
        restoreFocus.current = false;
      });
      return () => cancelAnimationFrame(frame);
    }
  }, [inline, open]);
  useLayoutEffect(() => {
    if (!inline || !open || !panelContainer) return;
    const rail = railSurface.current;
    const controls = () =>
      [
        ...(rail?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]):not([type=hidden]), textarea:not([disabled]), [tabindex="0"]',
        ) ?? []),
      ].filter((node) => node.getClientRects().length > 0);
    const handle = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        document.querySelector(
          'dialog[open], .ant-select-open, .ant-dropdown:not(.ant-dropdown-hidden), .ant-popover:not(.ant-popover-hidden)',
        )
      )
        return;
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        void close();
      }
    };
    const focus = requestAnimationFrame(() => (controls()[0] ?? rail)?.focus());
    rail?.addEventListener('keydown', handle);
    return () => {
      cancelAnimationFrame(focus);
      rail?.removeEventListener('keydown', handle);
    };
  }, [inline, open, panelContainer]);
  const dirty = snapshot.dirty;
  const live = useRef({ dirty, busy, save: async () => false });
  const scope = `sound:${projectId}`;
  const line = doc?.dialogue.find((l) => l.id === selected);
  const capabilityEpoch = useRef(0);
  const loadCapability = useCallback(async () => {
    const epoch = ++capabilityEpoch.current;
    setCapabilityStatus('loading');
    setCapabilityError('');
    try {
      const next = await api.enabled();
      if (mounted.current && epoch === capabilityEpoch.current) {
        setEnabled(next);
        setCapabilityStatus('ready');
      }
    } catch (cause) {
      if (mounted.current && epoch === capabilityEpoch.current) {
        setCapabilityError(errorMessage(cause));
        setCapabilityStatus('error');
      }
    }
  }, [api]);
  useEffect(() => {
    mounted.current = true;
    void loadCapability();
    return () => {
      mounted.current = false;
      capabilityEpoch.current++;
    };
  }, [loadCapability]);
  useEffect(() => {
    registerBarrier({
      hasUnsettled: () => session.hasUnsettled() || live.current.busy,
      flush: () => live.current.save(),
    });
    return () => registerBarrier(null);
  }, [registerBarrier, session]);
  useEffect(() => {
    if (!open || !selected) return;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const next = await api.candidates(selected, controller.signal);
        if (!controller.signal.aborted) setCandidates(next);
      } catch (e) {
        if (!controller.signal.aborted) setNotice(errorMessage(e));
      }
    };
    setCandidates([]);
    void poll();
    const timer = setInterval(() => void poll(), 2500);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [api, open, selected, pollVersion]);
  useEffect(() => {
    if (!open || !extractId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await api.extraction(extractId, controller.signal);
        if (controller.signal.aborted) return;
        setExtraction(result);
        if (['queued', 'running'].includes(result.status)) timer = setTimeout(() => void poll(), 2500);
      } catch (e) {
        if (!controller.signal.aborted) setNotice(errorMessage(e));
      }
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [api, open, extractId]);
  function replace(next: SoundState) {
    session.accept(next);
  }
  function edit(next: SoundDocument) {
    if (disabled || lock.current) return;
    setDoc(next);
  }
  function replaceDocument(next: SoundDocument) {
    const current = session.getSnapshot().value;
    if (!current || readOnly) return;
    saveRecovery(`${recoveryScope}:previous`, {
      format: 1,
      base_version: current.row_version,
      document: {
        document: current.document,
        reviewed: !current.needs_review,
        timeline_hash: current.timeline_hash,
      },
      previous_recovery: readRecoveryText(recoveryScope),
    });
    setDoc(next);
  }
  async function action(work: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setNotice('');
    try {
      await work();
    } catch (e) {
      if (mounted.current) setNotice(e instanceof CreationRecoveryError ? e.message : errorMessage(e));
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function saveDocument(markReviewed = reviewed): Promise<SoundState | null> {
    if (!state || !doc || readOnly || !(await flushVideo())) return null;
    setReviewed(markReviewed);
    if (!(await (session.getSnapshot().status === 'error' ? session.retry() : session.flush()))) {
      setNotice(session.getSnapshot().message);
      return null;
    }
    await onSaved();
    return session.getSnapshot().value;
  }
  live.current = {
    dirty: session.hasUnsettled(),
    busy,
    save: async () => !readOnly && (await session.flush()),
  };
  async function begin() {
    await action(async () => {
      if (!(await flushVideo())) return;
      setOpen(true);
      await session.load();
      try {
        setExtractId(sessionStorage.getItem(`${scope}:extraction`) ?? '');
      } catch {
        /* history remains in task center */
      }
    });
  }
  useEffect(() => {
    if (
      !requestedLine ||
      !/^[a-zA-Z0-9_-]{1,64}$/.test(requestedLine) ||
      !enabled ||
      locatedSource.current === `${projectId}:${requestedLine}`
    )
      return;
    locatedSource.current = `${projectId}:${requestedLine}`;
    void begin().then(() => {
      const current = session.getSnapshot().value;
      if (!mounted.current || !current) return;
      if (current.document.dialogue.some((item) => item.id === requestedLine)) setSelected(requestedLine);
      else setNotice('来源台词已删除或不再可用，请核对声音草稿。');
    });
  }, [projectId, requestedLine, enabled, session]);
  async function close() {
    try {
      if (!readOnly && !(await session.flush())) {
        setNotice(session.getSnapshot().message);
        return;
      }
      await onSaved();
      restoreFocus.current = true;
      setOpen(false);
    } catch (cause) {
      setNotice(errorMessage(cause));
    }
  }
  async function restoreSound() {
    await session.reload(true);
  }
  function backup() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify({ ...state, document: doc }, null, 2)], {
        type: 'application/json',
      }),
    );
    const a = document.createElement('a');
    a.href = url;
    a.download = 'sound-draft.json';
    a.click();
    URL.revokeObjectURL(url);
  }
  function backupRecovery() {
    try {
      const content = session.exportRecovered();
      if (!content) return;
      const url = URL.createObjectURL(new Blob([content], { type: 'application/json;charset=utf-8' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = 'sound-recovery.json';
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '无法下载声音恢复记录。');
    }
  }
  async function refreshAudio() {
    const fresh = await api.get();
    if (!mounted.current) return;
    setState((previous) =>
      previous
        ? {
            ...previous,
            media: { ...previous.media, ...fresh.media },
            uploads: previous.uploads.map((item) => {
              const updated = fresh.uploads.find((next) => next.media_id === item.media_id);
              return updated ? { ...item, url: updated.url } : item;
            }),
          }
        : previous,
    );
    setAudioError('');
    setPollVersion((value) => value + 1);
  }
  async function generate() {
    if (!line || !line.voice.trim() || !line.text.trim()) {
      setNotice('请填写台词和音色。');
      return;
    }
    if (
      candidates.length &&
      !(await confirmAction('将按当前台词新建配音任务，可能产生费用。已有候选仍会保留，是否继续？', {
        className: inline ? 'standard-creation-dialog' : undefined,
      }))
    )
      return;
    await action(async () => {
      const saved = dirty || state?.row_version === '0' || !state ? await saveDocument(false) : state;
      if (!saved) return;
      const body = {
        ...(line.config_id ? { config_id: line.config_id } : {}),
        source: {
          scene: 'dialogue_audio' as const,
          project_id: projectId,
          row_version: String(saved.row_version),
          line_id: line.id,
        },
      };
      await generations.generateAudio(body, await requestAttempt(`${scope}:${line.id}:speech`, body, attemptStorage()));
      clearAttempt(`${scope}:${line.id}:speech`, attemptStorage());
      setPollVersion((n) => n + 1);
    });
  }
  const active = candidates.some(
    (c) =>
      ['queued', 'running'].includes(c.status) ||
      c.can_resume ||
      ['acceptance_unknown', 'provider_acceptance_unknown', 'provider_unknown', 'message_delivery_unknown'].includes(
        c.error?.code ?? '',
      ),
  );
  if (capabilityStatus === 'loading') {
    const trigger = <AssemblyAction label="声音、字幕和配乐" icon={<Volume2 size={16} />} disabled loading />;
    return renderToolbar ? renderToolbar(trigger) : trigger;
  }
  if (capabilityStatus === 'error') {
    const trigger = (
      <AssemblyAction label="声音、字幕和配乐" icon={<Volume2 size={16} />} onClick={() => void loadCapability()} />
    );
    return (
      <>
        {renderToolbar ? renderToolbar(trigger) : trigger}
        <StudioFeedback
          type="warning"
          noticeKey={`${scope}:capability`}
          message="声音功能读取失败"
          description={capabilityError}
          actions={<Button onClick={() => void loadCapability()}>重新读取声音功能</Button>}
        />
      </>
    );
  }
  if (!enabled) return renderToolbar?.(null) ?? null;
  const title = state?.mode === 'native' ? '原声、字幕和配乐' : '配音、字幕和配乐';
  const actions = (
    <div className={`sound-actions${inline ? ' sound-save-actions' : ''}`}>
      {inline ? (
        <Dropdown
          trigger={['click']}
          overlayClassName="standard-creation-menu standard-creation-popup"
          menu={{
            items: [
              { key: 'download', label: '下载草稿', onClick: backup },
              ...(session.hasPrevious()
                ? [
                    {
                      key: 'backup',
                      label: '下载本机备份',
                      onClick: backupRecovery,
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
            aria-label="声音草稿操作"
            title="声音草稿操作"
          />
        </Dropdown>
      ) : (
        <>
          <AssemblyAction label="下载草稿" icon={<Download size={16} />} onClick={backup} />
          {session.hasPrevious() && (
            <Button type="text" onClick={backupRecovery}>
              下载本机备份
            </Button>
          )}
        </>
      )}
      <Tooltip title="声音或剪辑变化后，请重新核对时间，再勾选确认。" trigger={['hover', 'focus']}>
        <Checkbox
          aria-label="已核对当前剪辑的声音与字幕时间"
          disabled={disabled || busy}
          checked={reviewed}
          onChange={(e) => setReviewed(e.target.checked)}
        >
          时间已核对
        </Checkbox>
      </Tooltip>
      <AssemblyAction
        type="primary"
        label="保存声音草稿"
        icon={<Save size={16} />}
        aria-busy={busy}
        disabled={disabled || !doc}
        loading={busy}
        onClick={() =>
          void action(async () => {
            await saveDocument();
          })
        }
      />
    </div>
  );
  const content = (
    <>
      {!inline && (
        <SoundHelp title="时间均为成片中的绝对毫秒。修改视频后需重新核对；保存后通过“合成预览”检查混音与字幕。" />
      )}
      {audioError && (
        <StudioFeedback
          type="warning"
          noticeKey={`${scope}:audio`}
          message={audioError}
          actions={
            <Button loading={busy} disabled={busy} onClick={() => void action(refreshAudio)}>
              刷新试听地址
            </Button>
          }
        />
      )}
      {recoveryError && (
        <StudioFeedback
          type="warning"
          noticeKey={`${scope}:recovery`}
          message={recoveryError}
          actions={
            <div className="sound-actions">
              <Button onClick={backupRecovery}>下载完整恢复稿</Button>
              <Button
                disabled={busy}
                onClick={() =>
                  void action(async () => {
                    await session.reload(false);
                  })
                }
              >
                使用服务端版本
              </Button>
            </div>
          }
        />
      )}
      {recoveredDraft && (
        <StudioFeedback
          type="warning"
          noticeKey={`${scope}:conflict`}
          message="声音存在不同版本，本机输入已保留"
          actions={
            <div className="sound-actions">
              <Button disabled={busy || readOnly} onClick={() => void action(restoreSound)}>
                以当前版本保留本机修改
              </Button>
              <Button
                disabled={busy}
                onClick={() =>
                  void action(async () => {
                    await session.reload(false);
                  })
                }
              >
                使用服务端版本
              </Button>
            </div>
          }
        />
      )}
      {(notice || snapshot.message) && !recoveryError && !recoveredDraft && (
        <StudioFeedback
          type="error"
          noticeKey={`${scope}:editing`}
          message={notice || snapshot.message}
          actions={
            <>
              <Button disabled={busy} onClick={() => void session.retry()}>
                重试保存
              </Button>
              <Button
                disabled={busy}
                onClick={() =>
                  void action(async () => {
                    await session.reload(false);
                  })
                }
              >
                使用服务端版本
              </Button>
            </>
          }
        />
      )}
      {state?.needs_review && (
        <StudioFeedback
          type="warning"
          noticeKey={`${scope}:review`}
          quiet
          persistent
          message="声音或剪辑有变化，导出前请核对时间并勾选确认。"
        />
      )}
      {state?.mode === 'native' && doc && (
        <section className="native-voice" aria-label="原生视频后期声音">
          <SoundHelp
            label="原声与配乐压低说明"
            title="对白来自视频原声，不叠加独立配音。字幕时间需逐句校对，视频内对白和环境音不能分别调节。压低区间内配乐降为设定音量的四分之一，仅在启用“对白期间自动降低配乐音量”时生效。"
          />
          <AssemblyAction
            label="从采用视频的台词创建字幕草稿"
            icon={<Captions size={16} />}
            disabled={busy || disabled}
            onClick={() =>
              void action(async () => {
                const draft = await api.nativeSubtitles();
                if (draft.timeline_hash !== state.timeline_hash) throw new Error('剪辑已变化，请重新载入声音面板。');
                if (mounted.current) replaceDocument({ ...doc, subtitles: draft.subtitles });
              })
            }
          />
          <AssemblyAction
            label="用已校对字幕设置对白压低区间"
            icon={<Volume2 size={16} />}
            disabled={busy || disabled || !doc.subtitles.length}
            onClick={() =>
              edit({
                ...doc,
                native_ducking: doc.subtitles.map((s) => ({ ...s })),
              })
            }
          />
          {(doc.native_ducking ?? []).map((range, i) => (
            <div className="sound-subtitle" key={i}>
              <label>
                对白开始毫秒
                <InputNumber
                  min={0}
                  max={3600000}
                  precision={0}
                  disabled={busy || disabled}
                  value={range.start_ms}
                  onChange={(v) =>
                    edit({
                      ...doc,
                      native_ducking: doc.native_ducking!.map((r, n) => (n === i ? { ...r, start_ms: v ?? 0 } : r)),
                    })
                  }
                />
              </label>
              <label>
                对白结束毫秒
                <InputNumber
                  min={1}
                  max={3600000}
                  precision={0}
                  disabled={busy || disabled}
                  value={range.end_ms}
                  onChange={(v) =>
                    edit({
                      ...doc,
                      native_ducking: doc.native_ducking!.map((r, n) => (n === i ? { ...r, end_ms: v ?? 1 } : r)),
                    })
                  }
                />
              </label>
              <span>{range.text}</span>
              <Button
                disabled={busy || disabled}
                onClick={() =>
                  edit({
                    ...doc,
                    native_ducking: doc.native_ducking!.filter((_, n) => n !== i),
                  })
                }
              >
                移除区间
              </Button>
            </div>
          ))}
        </section>
      )}
      {!doc || !state ? (
        <Spin />
      ) : (
        <fieldset disabled={disabled || busy} className="sound-fieldset">
          <Tabs
            items={[
              {
                key: 'dialogue',
                label: `配音 · ${doc.dialogue.length}`,
                children: (
                  <SoundDialogueEditor
                    state={state}
                    busy={busy}
                    disabled={disabled}
                    inline={inline}
                    onEdit={edit}
                    scope={scope}
                    selection={{ selected, setSelected }}
                    extractionState={{
                      extraction,
                      extractId,
                      onExtract: (textConfig) =>
                        action(async () => {
                          const body = {
                            ...(textConfig ? { config_id: textConfig } : {}),
                            source: { scene: 'dialogue_extract' as const, project_id: projectId },
                            parameters: {},
                          };
                          // 受理结果不明时必须沿用原请求键；拿到回执后才清除本机记录。
                          const receipt = await generations.generateText(
                            body,
                            await requestAttempt(scope + ':extract', body, attemptStorage()),
                          );
                          clearAttempt(scope + ':extract', attemptStorage());
                          setExtractId(receipt.generation_id);
                          setExtraction(null);
                          try {
                            sessionStorage.setItem(scope + ':extraction', receipt.generation_id);
                          } catch {
                            /* 任务中心仍保留回执。 */
                          }
                        }),
                      onAdoptExtraction: () =>
                        action(async () => {
                          if (!extraction?.dialogue) return;
                          replaceDocument({
                            ...doc,
                            dialogue: extraction.dialogue.map((l) => ({
                              ...l,
                              voice: state.voice_defaults.voices[l.character] ?? '',
                            })),
                          });
                          setSelected(extraction.dialogue[0]?.id ?? '');
                          setExtraction(null);
                        }),
                    }}
                    speech={{
                      candidates,
                      active,
                      generate,
                      dirty,
                      resume: (id) =>
                        action(async () => {
                          await generations.resume(id);
                          setPollVersion((n) => n + 1);
                        }),
                      adopt: (lineId, mediaId) =>
                        action(async () => {
                          replace(await api.adopt(state.row_version, lineId, mediaId));
                          await onSaved();
                        }),
                      onAudioError: () => setAudioError('试听地址无法播放，请刷新地址后重试，当前编辑已保留。'),
                    }}
                    onSaveVoice={(character, voice) =>
                      action(async () => {
                        const voices = await api.voices({
                          row_version: state.voice_defaults.row_version,
                          voices: { ...state.voice_defaults.voices, [character]: voice },
                        });
                        setState({ ...state, voice_defaults: voices });
                      })
                    }
                  />
                ),
              },
              {
                key: 'subtitles',
                label: `字幕 · ${doc.subtitles.length}`,
                children: (
                  <SoundSubtitlesEditor
                    state={state}
                    busy={busy}
                    disabled={disabled}
                    inline={inline}
                    onEdit={edit}
                    onNotice={setNotice}
                    srtUrl={api.srtUrl}
                    onImport={(file) =>
                      action(async () => {
                        const bytes = await file.arrayBuffer();
                        const subtitles = await api.importSrt(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
                        setDoc({ ...doc, subtitles });
                        setReviewed(false);
                      })
                    }
                    onFill={() =>
                      action(async () => {
                        replaceDocument({
                          ...doc,
                          subtitles: doc.dialogue
                            .filter((l) => l.media_id && state.media[l.media_id])
                            .map((l) => ({
                              start_ms: l.start_ms,
                              end_ms: l.start_ms + state.media[l.media_id!].duration_ms,
                              text: l.text,
                            }))
                            .sort((a, b) => a.start_ms - b.start_ms),
                        });
                      })
                    }
                  />
                ),
              },
              {
                key: 'music',
                label: '配乐与混音',
                children: (
                  <SoundMixingEditor
                    state={state}
                    busy={busy}
                    disabled={disabled}
                    inline={inline}
                    onEdit={edit}
                    onMetadata={setState}
                    onNotice={setNotice}
                    onAudioError={setAudioError}
                    onUpload={(file) =>
                      action(async () => {
                        const music = await api.upload(file);
                        setDoc({
                          ...doc,
                          music: {
                            media_id: music.media_id,
                            start_ms: 0,
                            trim_in_ms: 0,
                            trim_out_ms: music.duration_ms,
                            volume: 0.2,
                            loop: false,
                            fade_in_ms: 500,
                            fade_out_ms: 500,
                            ducking: true,
                          },
                        });
                        setState({ ...state, media: { ...state.media, [music.media_id]: music } });
                        setReviewed(false);
                      })
                    }
                  />
                ),
              },
            ].filter(
              (item) =>
                (state.mode !== 'native' || item.key !== 'dialogue') &&
                (doc.schema_version !== 2 || item.key === 'dialogue'),
            )}
          />
        </fieldset>
      )}
    </>
  );
  const trigger = (
    <Tooltip title={inline && open ? '收起声音编辑' : '声音、字幕和配乐'} trigger={['hover', 'focus']}>
      <Button
        ref={entry}
        type="text"
        className="assembly-icon-action"
        icon={<Volume2 size={16} />}
        aria-label={inline && open ? '收起声音编辑' : '声音、字幕和配乐'}
        disabled={inline && open ? false : disabled}
        loading={inline && open ? false : busy}
        aria-expanded={inline ? open : undefined}
        aria-controls={inline ? panelId : undefined}
        onClick={() => {
          if (inline && open) void close();
          else void begin();
        }}
      />
    </Tooltip>
  );
  const rail = open && (
    <>
      <section
        ref={railSurface}
        id={panelId}
        className={`sound-inline${panelContainer ? ' sound-rail' : ''}`}
        aria-label={title}
        tabIndex={-1}
      >
        <StudioMessageProvider local getContainer={() => railSurface.current ?? document.body}>
          <ConfigProvider getPopupContainer={() => railSurface.current ?? document.body}>
            <header>
              <h3>{title}</h3>
              <div className="sound-header-actions">
                <SoundHelp title="修改剪辑后需重新核对声音与字幕时间；合成预览可检查实际混音与字幕。" />
                <StudioFeedbackIndicator />
                <AssemblyAction label="收起声音编辑" icon={<X size={16} />} onClick={() => void close()} />
              </div>
            </header>
            <div className="sound-rail-content">{content}</div>
            {actions}
          </ConfigProvider>
        </StudioMessageProvider>
      </section>
    </>
  );
  return (
    <>
      {renderToolbar ? renderToolbar(trigger) : trigger}
      {!open && notice && <StudioFeedback type="error" noticeKey={`${scope}:closed`} message={notice} />}
      {inline ? (
        panelContainer ? (
          createPortal(rail, panelContainer)
        ) : (
          rail
        )
      ) : (
        <StudioMessageProvider local getContainer={() => drawerSurface.current ?? document.body}>
          <Drawer
            open={open}
            width={960}
            title={title}
            extra={<StudioFeedbackIndicator />}
            onClose={close}
            maskClosable
            destroyOnHidden
            rootClassName="sound-drawer"
            footer={actions}
          >
            <div className="sound-drawer-body" ref={drawerSurface}>
              {content}
            </div>
          </Drawer>
        </StudioMessageProvider>
      )}
    </>
  );
}
