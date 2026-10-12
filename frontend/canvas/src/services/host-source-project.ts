import type { CanvasSourceEpisodeRead, CanvasSourceEpisodeSummary, CanvasSourceProjectContext, CanvasSourceProjectRead } from "@host/api/types/canvas-source-project";

export type { CanvasSourceEpisodeSummary, CanvasSourceProjectContext, CanvasSourceProjectRead };
export type CanvasSourceChapterPayload = { id: string; title: string; position: number; projectId: string; contentVersion: string };
type ReadOptions = { params: Record<string, string>; signal?: AbortSignal };
export type CanvasSourceProjectTransport = { get(path: string, options: ReadOptions): Promise<unknown> };
type HostSourceHttp = { get(path: string, options: ReadOptions & { headers: { "X-Canvas-Actor": string } }): Promise<{ data: unknown }> };

export function createCanvasSourceProjectTransport(host: HostSourceHttp, actor: string, assertCurrent: () => void): CanvasSourceProjectTransport {
    return {
        async get(path, options) {
            assertCurrent();
            if (!/^\/canvas-runtime\/canvas-projects\/[^/]+\/source-project(?:\/episodes\/[1-9][0-9]*)?$/.test(path)) throw new Error("来源项目读取路径无效");
            const response = await host.get(path, { ...options, headers: { "X-Canvas-Actor": actor } });
            assertCurrent();
            return response.data;
        },
    };
}

const maximumId = "18446744073709551615";
const identifier = (value: unknown): value is string => typeof value === "string" && /^[1-9][0-9]{0,19}$/.test(value) && (value.length < maximumId.length || value <= maximumId);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const exactKeys = (value: Record<string, unknown>, keys: readonly string[]) => Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const contextKeys = ["canvas_key", "canvas_row_version", "owning_project_id", "source_project_id"];

function contextValid(context: CanvasSourceProjectContext) {
    return typeof context.canvas_key === "string" && Array.from(context.canvas_key).length <= 64
        && /^[\p{Alphabetic}\p{Mark}\p{Decimal_Number}\p{Connector_Punctuation}\u200C\u200D-]+$/u.test(context.canvas_key)
        && [context.canvas_row_version, context.owning_project_id, context.source_project_id].every(identifier);
}
function sameContext(response: Record<string, unknown>, expected: CanvasSourceProjectContext) {
    return contextKeys.every(key => response[key] === expected[key as keyof CanvasSourceProjectContext]);
}
function episodeValid(value: unknown): value is CanvasSourceEpisodeSummary {
    return record(value) && exactKeys(value, ["id", "title", "position", "content_version", "kind"])
        && identifier(value.id) && identifier(value.content_version) && typeof value.title === "string"
        && Number.isSafeInteger(value.position) && (value.position as number) >= 1 && (value.position as number) <= 4294967295 && value.kind === "episode";
}
function capture(context: CanvasSourceProjectContext, assertCurrent: () => void) {
    assertCurrent();
    if (!contextValid(context)) throw new Error("画布尚未关联可读取的来源项目");
    return structuredClone(context);
}
function params(context: CanvasSourceProjectContext) {
    return { expected_canvas_row_version: context.canvas_row_version, expected_owning_project_id: context.owning_project_id, expected_source_project_id: context.source_project_id };
}
function assertUnchanged(context: CanvasSourceProjectContext, expected: CanvasSourceProjectContext, assertCurrent: () => void) {
    assertCurrent();
    if (JSON.stringify(context) !== JSON.stringify(expected)) throw new Error("来源项目读取上下文已变化，请重新读取");
}

export async function readCanvasSourceProject(transport: CanvasSourceProjectTransport, context: CanvasSourceProjectContext, assertCurrent: () => void): Promise<CanvasSourceProjectRead> {
    const expected = capture(context, assertCurrent);
    const response = await transport.get(`/canvas-runtime/canvas-projects/${encodeURIComponent(expected.canvas_key)}/source-project`, { params: params(expected) });
    assertUnchanged(context, expected, assertCurrent);
    if (!record(response) || !exactKeys(response, [...contextKeys, "project", "episodes", "capabilities"]) || !sameContext(response, expected)
        || !record(response.project) || !exactKeys(response.project, ["id", "row_version", "name", "synopsis", "aspect", "style_text"])
        || response.project.id !== expected.source_project_id || !identifier(response.project.row_version)
        || ![response.project.name, response.project.synopsis, response.project.style_text].every(value => typeof value === "string")
        || !["16:9", "9:16"].includes(response.project.aspect as string)
        || !Array.isArray(response.episodes) || !response.episodes.every(episodeValid)
        || new Set(response.episodes.map(episode => episode.id)).size !== response.episodes.length
        || new Set(response.episodes.map(episode => episode.position)).size !== response.episodes.length
        || !record(response.capabilities) || !exactKeys(response.capabilities, ["source_style_snapshot", "source_asset_folders", "source_assets"])
        || response.capabilities.source_style_snapshot !== "unavailable" || response.capabilities.source_asset_folders !== "unavailable" || response.capabilities.source_assets !== "not_included") throw new Error("来源项目与当前画布来源或版本不匹配，请重新读取");
    return structuredClone(response) as unknown as CanvasSourceProjectRead;
}

export async function readCanvasSourceEpisode(transport: CanvasSourceProjectTransport, context: CanvasSourceProjectContext, episode: CanvasSourceEpisodeSummary, assertCurrent: () => void, signal?: AbortSignal): Promise<CanvasSourceEpisodeRead> {
    const expected = capture(context, assertCurrent);
    if (!episodeValid(episode)) throw new Error("所选分集身份或版本无效，请重新选择");
    const selected = structuredClone(episode);
    const response = await transport.get(`/canvas-runtime/canvas-projects/${encodeURIComponent(expected.canvas_key)}/source-project/episodes/${selected.id}`, { params: { ...params(expected), expected_content_version: selected.content_version }, signal });
    assertUnchanged(context, expected, assertCurrent);
    if (JSON.stringify(episode) !== JSON.stringify(selected) || !record(response)
        || !exactKeys(response, [...contextKeys, "episode", "novel"]) || !sameContext(response, expected)
        || !episodeValid(response.episode) || !sameEpisode(response.episode, selected)
        || !record(response.novel) || !exactKeys(response.novel, ["state", "content_format", "plain_text"]) || response.novel.content_format !== "plain_text"
        || !(response.novel.state === "present" && typeof response.novel.plain_text === "string" || response.novel.state === "missing" && response.novel.plain_text === null)) throw new Error("所选分集正文或版本已变化，请刷新后重新选择");
    return structuredClone(response) as unknown as CanvasSourceEpisodeRead;
}

function sameEpisode(response: CanvasSourceEpisodeSummary, expected: CanvasSourceEpisodeSummary) {
    return Object.keys(expected).every(key => response[key as keyof CanvasSourceEpisodeSummary] === expected[key as keyof CanvasSourceEpisodeSummary]);
}

export function sourceEpisodePlainText(result: CanvasSourceEpisodeRead): string {
    if (result.novel.state === "missing") throw new Error("本集尚未保存小说正文，请先在标准工作台保存");
    return result.novel.plain_text;
}

export function sourceEpisodeChapter(episode: CanvasSourceEpisodeSummary, projectId: string): CanvasSourceChapterPayload {
    return { id: episode.id, title: episode.title, position: episode.position, projectId, contentVersion: episode.content_version };
}
