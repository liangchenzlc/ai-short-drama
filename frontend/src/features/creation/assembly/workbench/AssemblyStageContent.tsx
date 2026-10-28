import { AssemblyAction } from '@host/features/creation/assembly/export/AssemblyAction';
import { AssemblyAdoptionDialog } from '@host/features/creation/assembly/export/AssemblyAdoptionDialog';
import { AssemblyExportDialog } from '@host/features/creation/assembly/export/AssemblyExportDialog';
import { AssemblyExportHistory } from '@host/features/creation/assembly/export/AssemblyExportHistory';
import { AssemblySyncDialog } from '@host/features/creation/assembly/export/AssemblySyncDialog';
import { renderStageLabels as stages } from '@host/features/creation/assembly/export/presentation';
import { timecode } from '@host/features/creation/assembly/sessions/assembly-editing';
import { AssemblyWorkbench } from '@host/features/creation/assembly/workbench/AssemblyWorkbench';
import { useAssemblyStage } from '@host/features/creation/assembly/workbench/useAssemblyStage';
import { SoundPanel } from '@host/features/creation/sound/tracks/SoundPanel';
import { Icon } from '@host/shared/ui/Icon';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import type { AssemblyStageProps } from '@host/types/creation/assembly/workbench/stage';
import { Button, Dropdown, Progress, Spin, Tooltip } from 'antd';
import { Download, Ellipsis, History, Maximize2, Minimize2, RefreshCw, Upload, Video } from 'lucide-react';

export function AssemblyStageContent({ projectId, readOnly, registerBarrier, onStoryboard }: AssemblyStageProps) {
  const {
    applying,
    history,
    exportOpen,
    syncOpen,
    notice,
    preparationError,
    disabled,
    action,
    editor,
    setSyncOpen,
    renderRecoveryError,
    busy,
    previewBusy,
    damagedRender,
    backupRenderRecovery,
    discardRenderRecovery,
    loadRenderRecovery,
    pendingRender,
    reconcileRender,
    backupRecovery,
    backup,
    value,
    workspace,
    focused,
    setFocusMode,
    included,
    total,
    registerSound,
    soundTarget,
    setSoundOpen,
    openHistory,
    latestExport,
    current,
    setResult,
    api,
    blocked,
    active,
    prepareExport,
    preparation,
    needsThumbnails,
    clips,
    retry,
    soundOpen,
    result,
    preview,
    previewJob,
    previewStatus,
    setSoundTarget,
    previewError,
    setPreviewStatus,
    activeExports,
    flushRenderDraft,
    setApplying,
    stale,
    setExportOpen,
    submitExport,
    displayedHistory,
    moreHistory,
    setHistory,
    setMoreHistory
  } = useAssemblyStage({ projectId, readOnly, registerBarrier, onStoryboard });
  const feedbackTarget = applying ? 'apply' : history ? 'history' : exportOpen ? 'export' : syncOpen ? 'sync' : 'main';
  const workspaceFeedback = (
    <>
      {notice && <StudioFeedback type="error" noticeKey={`assembly:${projectId}:action`} message={notice} />}
      {preparationError && (
        <StudioFeedback
          type="warning"
          noticeKey={`assembly:${projectId}:preparation-error`}
          quiet
          message={preparationError}
          actions={
            <Button
              disabled={disabled}
              onClick={() =>
                void action(async () => {
                  if (await editor.flush()) {
                    await editor.load();
                    setSyncOpen(true);
                  }
                })
              }
            >
              同步分镜视频
            </Button>
          }
        />
      )}
      {renderRecoveryError && (
        <StudioFeedback
          type="warning"
          noticeKey={`assembly:${projectId}:render-recovery`}
          message="合成请求恢复记录无法读取"
          description={
            <>
              <p>
                {renderRecoveryError}{' '}
                请先下载原请求记录，并检查当前作品的导出和预览记录。清除后将失去原请求编号的核对依据；若后台已受理，再发起合成可能重复制作。
              </p>
              <div className="assembly-inline-actions">
                <Button disabled={busy || previewBusy || damagedRender === null} onClick={backupRenderRecovery}>
                  下载完整合成请求记录
                </Button>
                {damagedRender !== null && (
                  <Button danger disabled={busy || previewBusy} onClick={() => void action(discardRenderRecovery)}>
                    清除损坏的本机记录
                  </Button>
                )}
                <Button disabled={busy || previewBusy} onClick={loadRenderRecovery}>
                  重新读取恢复记录
                </Button>
              </div>
            </>
          }
        />
      )}
      {pendingRender && (
        <StudioFeedback
          type="warning"
          noticeKey={`assembly:${projectId}:pending-render`}
          message="上次合成请求尚未核对"
          description="将使用原参数和原请求编号核对受理结果，避免重复制作。当前编辑仍保留。"
          actions={
            <Button loading={busy} disabled={disabled} onClick={() => void action(reconcileRender)}>
              核对原合成请求
            </Button>
          }
        />
      )}
      {editor.connectionError && (
        <StudioFeedback
          type="warning"
          noticeKey={`assembly:${projectId}:connection`}
          message="成片进度暂时无法同步"
          description={`${editor.connectionError} 当前显示上次同步的进度，编辑草稿仍保留。${editor.lastSyncedAt ? `最近同步：${new Date(editor.lastSyncedAt).toLocaleTimeString('zh-CN')}` : ''}`}
          actions={
            <Button
              loading={busy}
              disabled={disabled}
              onClick={() =>
                void action(async () => {
                  await editor.load();
                })
              }
            >
              重新连接
            </Button>
          }
        />
      )}
      {editor.recoveryError && (
        <StudioFeedback
          type="warning"
          noticeKey={`assembly:${projectId}:draft-recovery`}
          message={editor.recoveryError}
          actions={
            <div className="assembly-inline-actions">
              <Button onClick={editor.recoveryBlocked ? backupRecovery : backup}>
                {editor.recoveryBlocked ? '下载完整恢复稿' : '下载草稿'}
              </Button>
              {editor.recoveryBlocked && (
                <Button
                  disabled={busy}
                  onClick={() =>
                    void action(async () => {
                      await editor.discardRecovered();
                    })
                  }
                >
                  使用服务端版本
                </Button>
              )}
            </div>
          }
        />
      )}
      {editor.recoveredDraft && (
        <StudioFeedback
          type="warning"
          noticeKey={`assembly:${projectId}:draft-conflict`}
          message="剪辑存在不同版本，本机输入已保留"
          description={`恢复稿基准版本 ${editor.recoveredDraft.base_version}，当前服务端版本 ${value?.assembly?.row_version}。恢复将重新读取最新版本，并核对片段媒体；无效来源仍需处理。`}
          actions={
            <div className="assembly-inline-actions">
              <Button
                disabled={readOnly || busy || previewBusy}
                onClick={() =>
                  void action(async () => {
                    await editor.restoreRecovered();
                  })
                }
              >
                以当前版本保留本机修改
              </Button>
              <Button
                disabled={busy}
                onClick={() =>
                  void action(async () => {
                    await editor.discardRecovered();
                  })
                }
              >
                使用服务端版本
              </Button>
            </div>
          }
        />
      )}
      {editor.error && !editor.recoveryError && !editor.recoveredDraft && (
        <StudioFeedback
          type="error"
          noticeKey={`assembly:${projectId}:editor-error`}
          message={editor.error}
          description={
            value ? '当前输入已保留，可继续切换；版本不同请核对后选择。' : '成片工作台未能载入，请检查连接后重新加载。'
          }
          actions={
            <div className="assembly-inline-actions">
              {value && <Button onClick={backup}>下载草稿</Button>}
              {editor.status === 'conflict' ? (
                <Button
                  disabled={disabled}
                  onClick={() =>
                    void action(async () => {
                      await editor.restoreRecovered();
                    })
                  }
                >
                  以当前版本保留本机修改
                </Button>
              ) : (
                <Button
                  disabled={disabled}
                  onClick={() =>
                    void action(async () => {
                      await editor.retrySave();
                    })
                  }
                >
                  {value ? '重试保存' : '重新加载'}
                </Button>
              )}
              {value && (
                <Button
                  disabled={disabled}
                  onClick={() =>
                    void action(async () => {
                      await editor.reload();
                    })
                  }
                >
                  使用服务端版本
                </Button>
              )}
            </div>
          }
        />
      )}
    </>
  );

  return (
    <div
      ref={workspace}
      className={`assembly-workspace${focused ? ' is-focused' : ''}`}
      onKeyDown={(event) => {
        if (
          event.key === 'Escape' &&
          focused &&
          !event.defaultPrevented &&
          !document.fullscreenElement &&
          !document.querySelector('dialog[open], [role="dialog"], [role="combobox"][aria-expanded="true"], .ant-dropdown:not(.ant-dropdown-hidden), .ant-popover:not(.ant-popover-hidden)') &&
          // Portal 中的声音面板和菜单先处理 Escape，不能同时退出父级专注模式。
          !(event.target as HTMLElement).closest('input, textarea, select, .sound-rail')
        ) {
          event.preventDefault();
          setFocusMode(false);
        }
      }}
    >
      <header className="assembly-heading">
        <div className="assembly-heading-title">
          <Video size={17} />
          <h2>成片导出</h2>
          {value?.assembly && (
            <span className="assembly-creation-meta">
              {value.assembly.aspect === 'auto' ? '自动' : value.assembly.aspect}
              <span aria-hidden="true">/</span>
              {included.length} 段<span aria-hidden="true">/</span>
              {timecode(total)}
            </span>
          )}
        </div>
        {value?.assembly && (
          <div className="assembly-heading-actions" role="group" aria-label="成片设置与同步">
            <SoundPanel
              key={`${projectId}`}
              projectId={projectId}
              disabled={disabled}
              readOnly={readOnly}
              flushVideo={editor.flush}
              onSaved={editor.load}
              registerBarrier={registerSound}
              panelContainer={soundTarget}
              onOpenChange={setSoundOpen}
            />
            <AssemblyAction
              label="同步分镜视频"
              icon={<RefreshCw size={16} />}
              disabled={disabled}
              onClick={() =>
                void action(async () => {
                  if (await editor.flush()) {
                    await editor.load();
                    setSyncOpen(true);
                  }
                })
              }
            />
            <AssemblyAction
              label="导出记录"
              icon={<History size={16} />}
              aria-busy={busy}
              loading={busy}
              onClick={() => void action(openHistory)}
            />
            {latestExport?.status === 'succeeded' && (
              <div className="assembly-latest-export" role="status" aria-label="最近导出已完成">
                <Button
                  size="small"
                  type="text"
                  title={`导出完成 · ${new Date(latestExport.created_at).toLocaleString()} · ${latestExport.media_id === current?.media_id ? '当前已采用' : '尚未采用'}`}
                  onClick={() => setResult(latestExport)}
                >
                  播放最新导出
                </Button>
                <AssemblyAction
                  label="下载最新导出 MP4"
                  icon={<Download size={16} />}
                  href={api.download(latestExport.id)}
                />
              </div>
            )}
            <AssemblyAction
              className="assembly-focus-toggle"
              label={focused ? '退出专注剪辑' : '专注剪辑'}
              icon={focused ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
              aria-pressed={focused}
              onClick={() => setFocusMode(!focused)}
            />
            {editor.hasPrevious() && (
              <Dropdown
                trigger={['click']}
                overlayClassName="standard-creation-popup"
                menu={{
                  items: [
                    {
                      key: 'backup',
                      label: '下载本机备份',
                      icon: <Download size={15} />,
                      onClick: backupRecovery,
                    },
                  ],
                }}
              >
                <Button
                  type="text"
                  className="assembly-icon-action"
                  icon={<Ellipsis size={16} />}
                  aria-label="更多成片操作"
                  title="更多成片操作"
                />
              </Dropdown>
            )}
            <Tooltip
              title={
                blocked.length
                  ? `${blocked.length} 个片段需要处理`
                  : !included.length
                    ? '请先添加分镜视频'
                    : active
                      ? `导出进度 ${active.progress}%`
                      : '导出成片'
              }
              trigger={['hover', 'focus']}
            >
              <span className="assembly-export-entry" tabIndex={0}>
                <Button
                  type="primary"
                  aria-label="导出成片"
                  icon={<Upload size={16} />}
                  loading={busy || !!active}
                  disabled={disabled || !included.length || !!blocked.length || !!active || editor.status === 'error'}
                  onClick={() => void action(prepareExport)}
                >
                  {active ? `${active.progress}%` : null}
                </Button>
              </span>
            </Tooltip>
          </div>
        )}
      </header>
      {feedbackTarget === 'main' && workspaceFeedback}
      {!value && editor.status === 'loading' && (
        <div className="assembly-empty">
          <Spin />
          <p>正在载入成片工作台…</p>
        </div>
      )}
      {value && !value.assembly && (
        <div className="assembly-empty">
          <div className="assembly-empty-frame">
            <Icon name="film" size={32} />
          </div>
          <h3>还没有成片草稿</h3>
          <p>{value.source_count ? '使用本集分镜视频开始剪辑' : '创建草稿后添加视频或图片'}</p>
          <Button
            type="primary"
            disabled={disabled}
            loading={busy}
            onClick={() =>
              void action(async () => {
                editor.replace(await api.initialize());
              })
            }
          >
            创建成片草稿
          </Button>
          <Button type="link" onClick={onStoryboard}>
            返回分镜制作
          </Button>
        </div>
      )}
      {value?.assembly && (
        <>
          {preparation && (needsThumbnails || clips.some((c) => c.issue === 'preparing')) && (
            <StudioFeedback
              type="warning"
              noticeKey={`assembly:${projectId}:preparation`}
              quiet
              message="视频预览素材准备未完成，可重试；已就绪的视频仍可剪辑。"
              actions={
                <Button
                  disabled={disabled || preparation.can_retry === false}
                  onClick={() => void action(() => retry(preparation))}
                >
                  重试检测
                </Button>
              }
            />
          )}
          <div className={`assembly-stage-body${soundOpen ? ' is-sound-open' : ''}`}>
            <AssemblyWorkbench
              editor={editor}
              disabled={disabled}
              soundOpen={soundOpen}
              result={result}
              onResult={setResult}
              onPreview={() => void preview()}
              previewBusy={previewBusy || !!previewJob}
              previewProgress={previewStatus?.progress}
              focused={focused}
            />
            <div ref={setSoundTarget} className="assembly-sound-target" hidden={!soundOpen} />
          </div>
          {previewJob && (
            <StudioFeedback
              type={previewError ? 'warning' : 'info'}
              noticeKey={`assembly:${projectId}:preview:${previewJob}`}
              quiet
              persistent
              message={previewStatus?.cancel_requested ? '正在取消预览…' : '正在后台合成预览'}
              description={previewError || '可以继续编辑，预览使用提交时的版本。'}
              actions={
                <Button
                  disabled={disabled || previewStatus?.can_cancel === false || previewStatus?.cancel_requested}
                  onClick={() =>
                    void action(async () => {
                      const cancelled = await api.cancel(previewJob);
                      setPreviewStatus(cancelled);
                      await editor.load();
                    })
                  }
                >
                  取消预览
                </Button>
              }
            >
              {previewStatus && <Progress percent={previewStatus.progress} size="small" />}
            </StudioFeedback>
          )}
          {current && (
            <StudioFeedback
              type="info"
              noticeKey={`assembly:${projectId}:current`}
              quiet
              persistent
              message={`当前成片${current.is_stale ? ' · 草稿已有新修改' : ''}`}
              actions={
                <div className="assembly-inline-actions">
                  <Button type="link" onClick={() => setResult(current)}>
                    播放成片
                  </Button>
                  <a href={api.download(current.id)}>下载 MP4</a>
                </div>
              }
            />
          )}
          {activeExports.map((job) => (
            <StudioFeedback
              type="info"
              noticeKey={`assembly:${projectId}:export:${job.id}`}
              quiet
              persistent
              key={job.id}
              message={job.cancel_requested ? '正在取消合成…' : (stages[job.stage] ?? '正在合成')}
              description="可以继续编辑，当前导出使用提交时的版本。"
              actions={
                <Button
                  disabled={disabled || job.can_cancel === false || job.cancel_requested}
                  onClick={() =>
                    void action(async () => {
                      await api.cancel(job.id);
                      await editor.load();
                    })
                  }
                >
                  取消导出
                </Button>
              }
            >
              <Progress percent={job.progress} size="small" />
            </StudioFeedback>
          ))}
          {latestExport && ['failed', 'cancelled'].includes(latestExport.status) && (
            <StudioFeedback
              type={latestExport.status === 'failed' ? 'error' : 'info'}
              noticeKey={`assembly:${projectId}:latest-export:${latestExport.id}`}
              persistent
              message={latestExport.status === 'failed' ? '最近一次导出失败' : '最近一次导出已取消'}
              description={latestExport.error?.message ?? '编辑草稿已保留，可以重试这次导出，或调整后重新导出。'}
              actions={
                <div className="assembly-inline-actions">
                  <Button
                    disabled={disabled || latestExport.can_retry === false || !!active}
                    onClick={() => void action(() => retry(latestExport))}
                  >
                    重试这次导出
                  </Button>
                  <Button disabled={disabled} onClick={() => void action(openHistory)}>
                    查看导出记录
                  </Button>
                </div>
              }
            />
          )}
          {latestExport?.status === 'succeeded' && latestExport.media_id !== current?.media_id && (
            <StudioFeedback
              type="success"
              noticeKey={`assembly:${projectId}:latest-export:${latestExport.id}`}
              quiet
              persistent
              message={`最近一次导出已完成${latestExport.is_stale ? ' · 草稿已有修改' : ''}`}
              actions={
                <div className="assembly-inline-actions">
                  <Button onClick={() => setResult(latestExport)}>播放导出结果</Button>
                  <a href={api.download(latestExport.id)}>下载导出 MP4</a>
                  <Button
                    disabled={disabled}
                    onClick={() =>
                      void action(async () => {
                        if (!(await flushRenderDraft())) return;
                        setApplying({
                          ...latestExport,
                          is_stale: editor.latest()?.context_hash !== latestExport.context_hash,
                        });
                      })
                    }
                  >
                    设为当前成片
                  </Button>
                </div>
              }
            />
          )}
        </>
      )}
      {syncOpen && (
        <AssemblySyncDialog
          changes={value?.changes}
          busy={busy}
          feedback={feedbackTarget === 'sync' ? workspaceFeedback : null}
          onClose={() => setSyncOpen(false)}
          onSubmit={() =>
            action(async () => {
              if (await editor.flush()) {
                editor.replace(await api.sync(editor.latest()!));
                editor.clearHistory();
                setSyncOpen(false);
              }
            })
          }
        />
      )}
      {exportOpen && (
        <AssemblyExportDialog
          value={value}
          busy={busy}
          disabled={disabled}
          includedCount={included.length}
          total={total}
          blocked={!!blocked.length}
          stale={stale}
          feedback={feedbackTarget === 'export' ? workspaceFeedback : null}
          onClose={() => setExportOpen(false)}
          onAspect={(aspect) => editor.edit((state) => ({ ...state, assembly: { ...state.assembly!, aspect } }))}
          onResolution={(resolution) =>
            editor.edit((state) => ({ ...state, assembly: { ...state.assembly!, resolution } }))
          }
          onSubmit={(acknowledge) => action(() => submitExport(acknowledge))}
        />
      )}
      {history && (
        <AssemblyExportHistory
          history={history}
          displayedHistory={displayedHistory}
          moreHistory={moreHistory}
          busy={busy}
          disabled={disabled}
          active={!!active}
          currentMediaId={value?.assembly?.current_media_id}
          feedback={feedbackTarget === 'history' ? workspaceFeedback : null}
          onClose={() => setHistory(null)}
          download={api.download}
          onPlay={(job) => {
            setResult(job);
            setHistory(null);
          }}
          onApply={(job) =>
            action(async () => {
              if (!(await flushRenderDraft())) return;
              setApplying({ ...job, is_stale: editor.latest()?.context_hash !== job.context_hash });
            })
          }
          onRetry={(job) => action(() => retry(job))}
          onCancel={(job) =>
            action(async () => {
              await api.cancel(job.id);
              await openHistory();
              await editor.load();
            })
          }
          onRefresh={() => action(openHistory)}
          onMore={() =>
            action(async () => {
              const page = await api.history(history.length);
              setHistory([...history, ...page.items]);
              setMoreHistory(page.has_more);
            })
          }
        />
      )}
      {applying && (
        <AssemblyAdoptionDialog
          applying={applying}
          busy={busy}
          feedback={feedbackTarget === 'apply' ? workspaceFeedback : null}
          onClose={() => setApplying(null)}
          onSubmit={() =>
            action(async () => {
              if (!(await editor.flush())) return;
              await api.apply(applying.id, editor.latest()!, applying.is_stale);
              setApplying(null);
              await editor.load();
              if (history) await openHistory();
            })
          }
        />
      )}
    </div>
  );

}
