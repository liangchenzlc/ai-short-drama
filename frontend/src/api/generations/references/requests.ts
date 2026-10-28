import { http } from '@host/api/http';
import type { ReferenceImage, ReferenceImages } from '@host/types/generations/references/requests';
export async function uploadTaskReference(file: File): Promise<ReferenceImage> {
  const body = new FormData();
  body.append('file', file);
  return (
    await http.post<ReferenceImage>('/generation-references/uploads', body, {
      timeout: 120_000,
    })
  ).data;
}
export function generationReferences(kind: 'asset' | 'shot', ownerId: string) {
  const root = `/generation-references/${kind}/${encodeURIComponent(ownerId)}`;
  return {
    async list(signal?: AbortSignal) {
      return (await http.get<ReferenceImages>(root, { signal })).data;
    },
    async upload(file: File, version: string) {
      const body = new FormData();
      body.append('file', file);
      return (
        await http.post<ReferenceImages>(root, body, {
          headers: { 'If-Match': `"${version}"` },
        })
      ).data;
    },
    async remove(mediaId: string, version: string) {
      return (
        await http.delete<ReferenceImages>(`${root}/${encodeURIComponent(mediaId)}`, {
          headers: { 'If-Match': `"${version}"` },
        })
      ).data;
    },
  };
}
