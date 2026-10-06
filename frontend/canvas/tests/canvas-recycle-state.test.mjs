import assert from "node:assert/strict";
import test from "node:test";
import { mergeRecycledProjects, recycledProjectPreview } from "../src/services/canvas-recycle-state.ts";

const document = { id: "canvas-a", workspaceProjectId: "9007199254740999", revision: "9007199254741001",
    title: "回收作品", createdAt: "2026-10-05T00:00:00Z", updatedAt: "2026-10-05T01:00:00Z",
    nodes: [{ id: "n", type: "image", metadata: { storageKey: "resource:9007199254741011", content: "/api/v1/canvas-runtime/resources/9007199254741011/file" } }] };
const item = { source_key: document.id, project_id: document.workspaceProjectId, archive_key: "archive-key",
    deleted_at: "2026-10-05T02:00:00Z", source_document: document };

test("新设备从服务端目录取得完整快照，版本和资源 ID 保持十进制字符串", () => {
    const [result] = mergeRecycledProjects([], [item]);
    assert.equal(result.project.revision, "9007199254741001");
    assert.equal(result.project.nodes[0].metadata.storageKey, "resource:9007199254741011");
    assert.equal(result.archiveKey, "archive-key");
    assert.equal(result.deletedAt, item.deleted_at);
});

test("同一次归档保留本机完整快照及删除时间，新的归档代次替换旧快照", () => {
    const [local] = mergeRecycledProjects([], [item]);
    local.project = { ...document, title: "本机快照", nodes: [...document.nodes, { id: "local" }] };
    local.deletedAt = "2026-10-05T01:59:59Z";
    const [same] = mergeRecycledProjects([local], [item]);
    assert.equal(same.project, local.project);
    assert.equal(same.deletedAt, local.deletedAt);
    const next = { ...item, archive_key: "next-key", source_document: { ...document, revision: "9007199254741004" } };
    const [changed] = mergeRecycledProjects([local], [next]);
    assert.equal(changed.project, next.source_document);
    assert.equal(changed.archiveKey, "next-key");
    assert.deepEqual(mergeRecycledProjects([local], []), []);
});

test("损坏目录拒绝合并且不修改本机快照", () => {
    const local = mergeRecycledProjects([], [item]);
    const before = structuredClone(local);
    for (const patch of [{ source_key: "wrong" }, { project_id: "wrong" }, { deleted_at: "invalid" },
        { archive_key: "" }, { source_document: { ...document, revision: 2 } }]) {
        assert.throws(() => mergeRecycledProjects(local, [{ ...item, ...patch }]), /已有快照已保留/);
        assert.deepEqual(local, before);
    }
});

test("归档预览只改展示副本的图片与视频地址，恢复快照不携带授权查询参数", () => {
    const [local] = mergeRecycledProjects([], [item]);
    local.project = structuredClone(document);
    local.project.nodes.push({ id: "video", type: "video", metadata: { storageKey: "resource:9007199254741012",
        previewContent: "/api/v1/canvas-runtime/resources/9007199254741013/file",
        videoPreview: { content: "", storageKey: "resource:9007199254741014" } } });
    const before = structuredClone(local);
    const preview = recycledProjectPreview(local, id => `https://canvas.test/resources/${id}/file`);
    assert.notEqual(preview, local.project);
    for (const value of [preview.nodes[0].metadata.content, preview.nodes[1].metadata.content,
        preview.nodes[1].metadata.previewContent, preview.nodes[1].metadata.videoPreview.content]) {
        const url = new URL(value);
        assert.equal(url.searchParams.get("recycle_source_key"), document.id);
        assert.equal(url.searchParams.get("recycle_archive_key"), item.archive_key);
    }
    assert.equal(preview.nodes[0].metadata.storageKey, undefined);
    assert.deepEqual(local, before);
});
