import { http } from '@host/api/http';
import type {
  ScriptCandidate,
  ScriptDetail,
  ShotMutation,
  ShotRead,
  ShotSize,
  StoryboardApi,
  StoryboardApplyResult,
  StoryboardPage,
  StoryboardResultPage,
} from '@host/types/creation/storyboard/shots';
import type { Page } from '@host/types/generations/api';
export function storyboardApi(projectId: string): StoryboardApi {
  const root = `/projects/${encodeURIComponent(projectId)}`;
  return {
    async scripts(signal?: AbortSignal, offset = 0) {
      return (
        await http.get<Page<ScriptCandidate>>(`${root}/scripts`, {
          params: { offset, limit: 100 },
          signal,
        })
      ).data;
    },
    async script(id: string, signal?: AbortSignal) {
      return (await http.get<ScriptDetail>(`${root}/scripts/${encodeURIComponent(id)}`, { signal })).data;
    },
    async shots(signal?: AbortSignal, includeArchived = false, offset = 0, limit = 20) {
      return (
        await http.get<StoryboardPage>(`${root}/shots`, {
          params: { offset, limit, include_archived: includeArchived },
          signal,
        })
      ).data;
    },
    async shot(id: string, signal?: AbortSignal) {
      return (await http.get<ShotMutation>(`${root}/shots/${encodeURIComponent(id)}`, { signal })).data;
    },
    async create(storyboardVersion: string, key: string) {
      return (
        await http.post<ShotMutation>(
          `${root}/shots`,
          {
            storyboard_version: storyboardVersion,
            script: '',
            asset_ids: [],
            image_settings: {
              resolution: '2K',
              aspect: null,
              layout: 'single',
            },
          },
          { headers: { 'Idempotency-Key': key } },
        )
      ).data;
    },
    async update(
      id: string,
      body: {
        row_version: string;
        title?: string;
        shot_size?: ShotSize | null;
        script?: string;
        duration_ms?: number;
        asset_ids?: string[];
        image_settings?: ShotRead['image_settings'];
        video_prompt?: string;
        video_settings?: ShotRead['video_settings'];
      },
    ) {
      return (await http.patch<ShotMutation>(`${root}/shots/${encodeURIComponent(id)}`, body)).data;
    },
    async remove(id: string, body: { row_version: string; storyboard_version: string }) {
      return (
        await http.delete<{ storyboard_version: string; deleted_id: string }>(
          `${root}/shots/${encodeURIComponent(id)}`,
          { data: body },
        )
      ).data;
    },
    async move(id: string, storyboardVersion: string, direction: -1 | 1) {
      return (
        await http.post(`${root}/shots/${encodeURIComponent(id)}/move`, {
          storyboard_version: storyboardVersion,
          direction,
        })
      ).data;
    },
    async order(storyboardVersion: string, shotIds: string[]) {
      return (
        await http.put<{ storyboard_version: string; ordered_ids: string[] }>(`${root}/shots/order`, {
          storyboard_version: storyboardVersion,
          shot_ids: shotIds,
        })
      ).data;
    },
    async resultShots(id: string, offset = 0, signal?: AbortSignal) {
      return (
        await http.get<StoryboardResultPage>(`${root}/storyboard-results/${encodeURIComponent(id)}/shots`, {
          params: { offset, limit: 20 },
          signal,
        })
      ).data;
    },
    async apply(
      generationId: string,
      body: {
        mode: 'append' | 'replace';
        content_version: string;
        storyboard_version: string;
        confirm_replace: boolean;
      },
    ) {
      return (
        await http.post<StoryboardApplyResult>(
          `${root}/storyboard-results/${encodeURIComponent(generationId)}/apply`,
          body,
        )
      ).data;
    },
  };
}
