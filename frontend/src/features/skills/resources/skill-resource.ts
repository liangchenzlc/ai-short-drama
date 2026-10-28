import { skillsApi } from '@host/api/skills/catalog';
import { resourceChecksum } from '@host/features/skills/library/skill-model';
import type { SkillResourceDraft } from '@host/types/skills/imports/skill-draft';
export async function readSkillResourceText(
  id: string,
  resource: SkillResourceDraft,
  signal?: AbortSignal,
): Promise<string> {
  if (resource.content !== null) return resource.content;
  if (!resource.origin_version || !resource.checksum_sha256) throw new Error('资料版本缺失，请重新读取 Skill。');
  let offset = 0;
  let result = '';
  while (true) {
    const part = await skillsApi.skillResource(id, resource.origin_version, resource.path, offset, signal);
    if (resource.kind === 'image') {
      if (
        part.skill_id !== id ||
        part.content_version !== resource.origin_version ||
        part.path !== resource.path ||
        part.kind !== 'image' ||
        part.encoding !== 'base64' ||
        part.checksum_sha256 !== resource.checksum_sha256 ||
        part.offset !== 0 ||
        part.next_offset !== null ||
        part.content.length > 174764
      )
        throw new Error('图片响应与冻结版本不一致。');
      if ((await resourceChecksum(part.content, 'base64')) !== resource.checksum_sha256)
        throw new Error('图片内容校验失败，请重试。');
      return part.content;
    }
    const characters = Array.from(part.content).length;
    if (
      part.skill_id !== id ||
      part.content_version !== resource.origin_version ||
      part.path !== resource.path ||
      part.kind !== resource.kind ||
      (part.encoding && part.encoding !== 'utf-8') ||
      part.checksum_sha256 !== resource.checksum_sha256 ||
      part.offset !== offset ||
      characters > 6000 ||
      result.length + part.content.length > 262144 ||
      (part.next_offset !== null && (part.next_offset !== offset + characters || part.next_offset <= offset))
    ) {
      throw new Error('资料响应与冻结版本不一致，请重新读取。');
    }
    result += part.content;
    if (part.next_offset === null) {
      if ((await resourceChecksum(result)) !== resource.checksum_sha256) throw new Error('文件内容校验失败，请重试。');
      return result;
    }
    offset = part.next_offset;
  }
}
