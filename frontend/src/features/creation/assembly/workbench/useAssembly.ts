import { assemblyApi } from '@host/api/creation/assembly/document';
import { errorMessage } from '@host/api/http';
import {
  followClips,
  normalizeEditor,
  normalizeSound,
} from '@host/features/creation/assembly/sessions/assembly-document';
import {
  assemblyDraft,
  assemblyVersion,
  restoreAssemblyDraft,
  validAssemblyDraft,
} from '@host/features/creation/assembly/sessions/assembly-recovery';
import {
  clearRecovery,
  creationScope,
  readRecovery,
  readRecoveryText,
  saveRecovery,
} from '@host/features/creation/shared/recovery/creation-recovery';
import { releaseStandardSession, retainStandardSession } from '@host/features/creation/shared/sessions/registry';
import { ResourceEditingSession } from '@host/features/creation/shared/sessions/resource-editing-session';
import { useAuth } from '@host/features/identity/session/AuthSession';
import type { AssemblyClip, AssemblyMaterial, AssemblyState } from '@host/types/creation/assembly/document';
import type { AssemblyDraftDocument } from '@host/types/creation/assembly/sessions/assembly-recovery';
import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
export function useAssembly(projectId: string, registerBarrier: (barrier: NavigationBarrier | null) => void) {
  const { user } = useAuth();
  const account = user?.id ?? 'anonymous';
  const key = `assembly:${projectId}`;
  const api = useMemo(() => assemblyApi(projectId), [projectId]);
  const session = useMemo(
    () =>
      retainStandardSession(
        account,
        key,
        () => {
          const scope = creationScope(`${key}:draft`, account);
          const readDraft = () => {
            return readRecovery(scope, validAssemblyDraft);
          };
          return new ResourceEditingSession<AssemblyState, AssemblyDraftDocument>({
            read: async () => {
              let recovered: AssemblyDraftDocument | undefined;
              try {
                recovered = readDraft()?.document;
              } catch {
                /* The session surfaces damaged recovery separately, retaining its original bytes. */
              }
              const ids = [
                ...(recovered?.clips.map((c) => c.media_id) ?? []),
                ...(recovered?.sound?.audio_clips?.map((c) => c.media_id) ?? []),
              ].filter((id): id is string => !!id);
              return normalizeEditor(await api.get(undefined, ids));
            },
            save: async (base, document) =>
              normalizeEditor(
                await api.save(
                  restoreAssemblyDraft(base, {
                    format: 1,
                    base_version: base.assembly!.row_version,
                    document,
                  }),
                ),
              ),
            version: assemblyVersion,
            document: (value) =>
              value.assembly
                ? assemblyDraft(value).document
                : {
                    assembly_id: '0',
                    aspect: 'auto',
                    resolution: '1080p',
                    clips: [],
                  },
            apply: (value, document) =>
              value.assembly
                ? restoreAssemblyDraft(value, {
                    format: 1,
                    base_version: value.assembly.row_version,
                    document,
                  })
                : value,
            recovery: {
              read: readDraft,
              save: (draft) => saveRecovery(scope, draft),
              clear: () => clearRecovery(scope),
              export: () => readRecoveryText(scope),
              previous: () => readRecoveryText(`${scope}:previous`),
              backup: (raw) => localStorage.setItem(`${scope}:previous`, raw),
            },
            pauseMs: 650,
          });
        },
        { label: '成片剪辑', projectId },
      ),
    [account, key, api, projectId],
  );
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const value = snapshot.value;
  const [connectionError, setConnectionError] = useState('');
  const [lastSyncedAt, setLastSyncedAt] = useState<number | null>(null);
  const past = useRef<AssemblyState[]>([]),
    future = useRef<AssemblyState[]>([]);
  const latestLoad = useRef(0);
  async function load() {
    const request = ++latestLoad.current;
    const fresh = await session.load(true, true).catch((cause) => {
      if (request !== latestLoad.current) return session.getSnapshot().value;
      throw cause;
    });
    if (!fresh) throw new Error(session.getSnapshot().message || '成片读取失败');
    if (request !== latestLoad.current) return fresh;
    session.updateMetadata((current) => ({
      ...current,
      jobs: fresh.jobs,
      current_work: fresh.current_work,
      materials: fresh.materials,
      voice_context_hash: fresh.voice_context_hash,
      sound:
        current.sound && fresh.sound
          ? {
              ...current.sound,
              media: { ...current.sound.media, ...fresh.sound.media },
            }
          : current.sound,
    }));
    setConnectionError('');
    setLastSyncedAt(Date.now());
    return fresh;
  }
  function edit(transform: (value: AssemblyState) => AssemblyState, record = true) {
    const before = session.getSnapshot().value;
    if (!before?.assembly) return;
    let next = transform(before);
    if (next === before) return;
    if (next.clips !== before.clips && before.sound && next.sound?.document === before.sound.document)
      next = {
        ...next,
        sound: {
          ...before.sound,
          document: followClips(
            before.clips ?? [],
            next.clips ?? [],
            normalizeSound(before.sound.document, before.clips ?? [], before.sound.media),
          ),
        },
      };
    if (record) {
      past.current = [...past.current.slice(-99), before];
      future.current = [];
    }
    session.edit(assemblyDraft(next).document);
  }
  function undo() {
    const previous = past.current.pop(),
      current = session.getSnapshot().value;
    if (!previous || !current) return;
    future.current.push(current);
    retainMedia(previous);
    session.edit(assemblyDraft(previous).document);
  }
  function redo() {
    const next = future.current.pop(),
      current = session.getSnapshot().value;
    if (!next || !current) return;
    past.current.push(current);
    retainMedia(next);
    session.edit(assemblyDraft(next).document);
  }
  function retainMedia(previous: AssemblyState) {
    session.updateMetadata((current) => ({
      ...current,
      materials: [
        ...new Map([...(previous.materials ?? []), ...(current.materials ?? [])].map((m) => [m.media_id, m])).values(),
      ],
      sound: current.sound
        ? {
            ...current.sound,
            media: { ...previous.sound?.media, ...current.sound.media },
          }
        : current.sound,
    }));
  }
  async function refreshMedia() {
    const history = [...past.current, ...future.current, session.getSnapshot().value].filter(
      (s): s is AssemblyState => !!s,
    );
    const ids = history
      .flatMap((s) => [
        ...(s.clips ?? []).map((c) => c.media_id),
        ...(s.sound?.document.audio_clips ?? []).map((c) => c.media_id),
      ])
      .filter((id): id is string => !!id);
    const fresh = await api.get(undefined, ids),
      sources = [
        ...(fresh.clips ?? []),
        ...(fresh.sources ?? []),
        ...(fresh.materials ?? [])
          .filter((m) => m.kind !== 'audio')
          .map((m) => ({
            media_id: m.media_id,
            url: m.url,
            poster: m.poster,
            filmstrip: null,
          })),
      ];
    const hydrate = (clips: AssemblyClip[]) =>
      clips.map((clip) => {
        const source = sources.find((item) => item.media_id === clip.media_id);
        return source
          ? {
              ...clip,
              url: source.url,
              poster: source.poster,
              filmstrip: source.filmstrip,
            }
          : clip;
      });
    session.updateMetadata((current) => ({
      ...current,
      clips: hydrate(current.clips ?? []),
      sources: hydrate(fresh.sources ?? []),
      materials: fresh.materials,
      sound: current.sound && fresh.sound ? { ...current.sound, media: fresh.sound.media } : current.sound,
      jobs: fresh.jobs,
      current_work: fresh.current_work,
    }));
    const refreshed = (state: AssemblyState) => ({
      ...state,
      clips: hydrate(state.clips ?? []),
      materials: fresh.materials,
      sound: state.sound
        ? {
            ...state.sound,
            media: { ...state.sound.media, ...fresh.sound?.media },
          }
        : state.sound,
    });
    past.current = past.current.map(refreshed);
    future.current = future.current.map(refreshed);
  }
  useEffect(() => {
    let cancelled = false,
      pollTimer: ReturnType<typeof setTimeout>;
    let failures = 0;
    void session.load();
    registerBarrier({
      hasUnsettled: session.hasUnsettled,
      flush: session.flush,
    });
    const poll = async () => {
      if (cancelled) return;
      if (session.getSnapshot().value) {
        try {
          await load();
          failures = 0;
        } catch (cause) {
          failures++;
          if (!cancelled) setConnectionError(errorMessage(cause));
        }
      }
      if (cancelled) return;
      const active = session.getSnapshot().value?.jobs?.some((job) => ['queued', 'running'].includes(job.status));
      const interval = document.hidden ? 30_000 : active ? 4000 : 15_000;
      const delay = failures ? Math.max(interval, Math.min(30_000, 4000 * 2 ** Math.min(failures, 3))) : interval;
      pollTimer = setTimeout(() => void poll(), delay);
    };
    pollTimer = setTimeout(() => void poll(), 4000);
    return () => {
      cancelled = true;
      clearTimeout(pollTimer);
      registerBarrier(null);
      releaseStandardSession(account, key);
    };
  }, [session, account, key]);
  return {
    api,
    value,
    status: snapshot.status,
    error: snapshot.message,
    connectionError,
    lastSyncedAt,
    recoveredDraft: snapshot.recovered,
    recoveryError: snapshot.recoveryBlocked ? snapshot.message : '',
    recoveryBlocked: snapshot.recoveryBlocked,
    edit,
    flush: session.flush,
    load,
    replace: (state: AssemblyState) => session.accept(normalizeEditor(state)),
    reload: () => (session.getSnapshot().loaded ? session.reload(false) : session.load(true)),
    undo,
    redo,
    refreshMedia,
    registerMaterial: (material: AssemblyMaterial) =>
      session.updateMetadata((current) => ({
        ...current,
        materials: [material, ...(current.materials ?? []).filter((m) => m.media_id !== material.media_id)],
        sound: current.sound
          ? {
              ...current.sound,
              media: {
                ...current.sound.media,
                [material.media_id]: {
                  url: material.url,
                  duration_ms: material.duration_ms ?? 0,
                },
              },
            }
          : current.sound,
      })),
    exportRecovered: session.exportRecovered,
    hasPrevious: session.hasPrevious,
    discardRecovered: () => session.reload(false),
    restoreRecovered: () => session.reload(true),
    canUndo: past.current.length > 0,
    canRedo: future.current.length > 0,
    clearHistory: () => {
      past.current = [];
      future.current = [];
    },
    latest: () => session.getSnapshot().value,
    retrySave: session.retry,
  };
}
