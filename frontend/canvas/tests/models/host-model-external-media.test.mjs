import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";

// External provider URLs are no longer materialized through a channel; older
// canvases keep their provider address and native players fetch it directly.
const stubs = {
    "@/lib/user-scope-guard": "export const captureUserScope = () => ({ userScope: 'account-A', epoch: 1 }); export const assertUserScope = () => {}; export const isUserScopeAbandonedError = () => false;",
    "@/lib/video-poster": "export const captureVideoPoster = async () => undefined; export const detectVideoAudioTrackFromBlob = async () => undefined;",
    "@/services/api/resources": "export class ResourceUploadError extends Error {} export const resourceIdFromStorageKey = () => undefined; export const resourceStorageKey = id => 'resource:' + id; export const resourceFileUrl = id => '/api/v1/canvas-runtime/resources/' + id + '/file'; export const uploadResourceFile = async () => { throw new Error('unexpected upload'); };",
    "@/services/api/request": "export const apiBaseURL = '/api/v1/canvas-runtime';",
    "@/services/image-storage": "export const uploadImage = async () => { throw new Error('unexpected image upload'); };",
    "@/services/resource-blob-cache": "export const cacheResourceObjectUrl = async () => ''; export const getCachedResourceBlob = async () => null; export const getCachedResourceObjectUrl = async () => ''; export const primeResourceBlobCache = async () => {};",
    "@/services/local-media-repository": "export const cleanupLocalMedia = async () => {}; export const deleteLocalMedia = async () => {}; export const getLocalMediaBlob = async () => null; export const resolveLocalMediaUrl = async () => ''; export const saveLocalMedia = async () => {}; export const setLocalMediaBlob = async () => {};",
    "@/services/workspace-resource-storage": "export const usesBrowserLocalResourceStore = () => false;",
    "@/stores/use-user-store": "export const useUserStore = Object.assign(() => undefined, { getState: () => ({ user: null }) });",
};
const bundled = await build({
    stdin: { contents: 'export { resolveMediaUrl } from "./src/services/file-storage.ts";', resolveDir: fileURLToPath(new URL("../..", import.meta.url)) },
    tsconfig: fileURLToPath(new URL("../../tsconfig.json", import.meta.url)), bundle: true, write: false, format: "esm", platform: "node", target: "node22",
    plugins: [{ name: "external-boundaries", setup(builder) {
        builder.onResolve({ filter: /^@\// }, args => args.path in stubs ? { path: args.path, namespace: "controlled" } : undefined);
        builder.onLoad({ filter: /.*/, namespace: "controlled" }, args => ({ contents: stubs[args.path], loader: "js" }));
    } }],
});
const { resolveMediaUrl } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`);

test("外部媒体地址按原样透传，不再经渠道物化下载", async () => {
    const url = "https://provider.example/old-video.mp4";
    assert.equal(await resolveMediaUrl(undefined, url), url);
    assert.equal(await resolveMediaUrl(undefined, "https://other.example/video.mp4"), "https://other.example/video.mp4");
    assert.equal(await resolveMediaUrl(undefined, ""), "");
});
