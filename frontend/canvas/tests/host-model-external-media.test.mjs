import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";

// Execute the actual source materializer; HTTP and durable media storage are
// controlled here. This does not validate provider downloads or MinIO.
const stubs = {
    "@/lib/user-scope-guard": "export const captureUserScope = () => ({ userScope: 'account-A', epoch: 1 }); export const assertUserScope = () => {}; export const isUserScopeAbandonedError = () => false;",
    "@/lib/video-poster": "export const captureVideoPoster = async () => undefined; export const detectVideoAudioTrackFromBlob = async () => undefined;",
    "@/services/api/resources": "export class ResourceUploadError extends Error {} export const resourceIdFromStorageKey = () => undefined; export const resourceStorageKey = id => 'resource:' + id; export const resourceFileUrl = id => '/api/v1/canvas-runtime/resources/' + id + '/file'; export const uploadResourceFile = (...args) => globalThis.mediaBoundary.upload(...args);",
    "@/services/api/request": "export const apiBaseURL = '/api/v1/canvas-runtime';",
    "@/services/image-storage": "export const uploadImage = async () => { throw new Error('unexpected image upload'); };",
    "@/services/api/channel-transport": "export const createChannelTransport = (...args) => globalThis.mediaBoundary.transport(...args);",
    "@/services/resource-blob-cache": "export const cacheResourceObjectUrl = async () => ''; export const getCachedResourceBlob = async () => null; export const getCachedResourceObjectUrl = async () => ''; export const primeResourceBlobCache = async () => {};",
    "@/services/local-media-repository": "export const cleanupLocalMedia = async () => {}; export const deleteLocalMedia = async () => {}; export const getLocalMediaBlob = async () => null; export const resolveLocalMediaUrl = async () => ''; export const saveLocalMedia = async () => {}; export const setLocalMediaBlob = async () => {};",
    "@/services/workspace-resource-storage": "export const usesBrowserLocalResourceStore = () => false;",
    "@/stores/use-user-store": "export const useUserStore = Object.assign(() => undefined, { getState: () => ({ user: null }) });",
};
const bundled = await build({
    stdin: { contents: 'export { resolveMediaUrl } from "./src/services/file-storage.ts"; export { sourceModelConfig } from "./src/services/host-model-config.ts"; export { useConfigStore } from "./src/stores/use-config-store.ts";', resolveDir: fileURLToPath(new URL("..", import.meta.url)) },
    tsconfig: fileURLToPath(new URL("../tsconfig.json", import.meta.url)), bundle: true, write: false, format: "esm", platform: "node", target: "node22",
    plugins: [{ name: "external-boundaries", setup(builder) {
        builder.onResolve({ filter: /^@\// }, args => args.path in stubs ? { path: args.path, namespace: "controlled" } : undefined);
        builder.onLoad({ filter: /.*/, namespace: "controlled" }, args => ({ contents: stubs[args.path], loader: "js" }));
    } }],
});
const { resolveMediaUrl, sourceModelConfig, useConfigStore } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`);
const model = (fields = {}) => ({ id: "9007199254740999", name: "宿主视频", model_key: "video", provider: "fixture", service_type: "video", enabled: true, has_api_key: true, credential_source: "beefapi", ...fields });

test("可信企业宿主模型物化旧视频，继续通过逻辑凭据引用而不暴露密钥", async () => {
    useConfigStore.getState().replaceConfig(sourceModelConfig({ row_version: "1", preferences: {}, models: [model()] }));
    const requests = [];
    const uploads = [];
    globalThis.mediaBoundary = { transport: (config, mode) => ({ getBlob: async url => { requests.push({ config, mode, url }); return new Blob(["video"], { type: "video/mp4" }); } }), upload: async (...args) => { uploads.push(args); return { id: "55" }; } };
    const url = "https://enterprise.beefapi.com/old-video.mp4";
    assert.equal(await resolveMediaUrl(undefined, url), "/api/v1/canvas-runtime/resources/55/file");
    assert.equal(requests[0].config.credentialRef, "host:9007199254740999");
    assert.equal(requests[0].config.apiKey, "");
    assert.equal(requests[0].mode, "video");
    assert.equal(requests[0].url, url);
    assert.equal(uploads[0][1], "video");
});

test("手工同名BeefAPI和禁用企业模型不冒用企业凭据，其他域名不触发物化", async () => {
    globalThis.mediaBoundary = { transport: () => assert.fail("untrusted source must not invoke enterprise download"), upload: () => assert.fail("unexpected upload") };
    for (const entry of [model({ name: "BeefAPI", credential_source: "manual" }), model({ enabled: false })]) {
        useConfigStore.getState().replaceConfig(sourceModelConfig({ row_version: "1", preferences: {}, models: [entry] }));
        const url = "https://enterprise.beefapi.com/unavailable.mp4";
        assert.equal(await resolveMediaUrl(undefined, url), url);
    }
    useConfigStore.getState().replaceConfig(sourceModelConfig({ row_version: "1", preferences: {}, models: [model()] }));
    const external = "https://other.example/video.mp4";
    assert.equal(await resolveMediaUrl(undefined, external), external);
});
