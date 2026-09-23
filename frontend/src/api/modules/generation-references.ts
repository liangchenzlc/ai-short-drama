import { http } from '../http';
export interface ReferenceImage { media_id: string; name: string; url: string; width?: number; height?: number }
export interface ReferenceImages { row_version: string; items: ReferenceImage[] }
export function generationReferences(kind: 'asset' | 'shot', ownerId: string) {
  const root = `/generation-references/${kind}/${encodeURIComponent(ownerId)}`;
  return {
    async list(signal?: AbortSignal) { return (await http.get<ReferenceImages>(root, { signal })).data; },
    async upload(file: File, version: string) {
      const body = new FormData(); body.append('file', file);
      return (await http.post<ReferenceImages>(root, body, { headers: { 'If-Match': `"${version}"` } })).data;
    },
    async remove(mediaId: string, version: string) {
      return (await http.delete<ReferenceImages>(`${root}/${encodeURIComponent(mediaId)}`, { headers: { 'If-Match': `"${version}"` } })).data;
    },
  };
}
