import { http } from '@host/api/http';
import type { CreationDraftTransport, CreationPurpose, UserCreationDraft } from '@host/types/creation/drafts/requests';
export function creationDraftsApi(
  projectId: string,
  objectId: string,
  purpose: CreationPurpose,
): CreationDraftTransport {
  const root = `/projects/${encodeURIComponent(projectId)}/drafts`;
  const target = `${root}/${encodeURIComponent(objectId)}/${purpose}`;
  return {
    async get() {
      return (await http.get<UserCreationDraft | null>(target)).data;
    },
    async create(value) {
      return (
        await http.post<UserCreationDraft>(root, {
          ...value,
          object_id: objectId,
          purpose,
        })
      ).data;
    },
    async update(row_version, value) {
      return (await http.patch<UserCreationDraft>(target, { ...value, row_version })).data;
    },
  };
}
