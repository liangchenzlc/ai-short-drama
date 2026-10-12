import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

// 执行真实骨骼完成回调；仅替换React、草稿状态和触发保存的边界。
const file = "../src/components/canvas/director/canvas-director-workbench.tsx";
const source = readFileSync(new URL(file, import.meta.url), "utf8");
const parsed = ts.createSourceFile(file, source, ts.ScriptTarget.ES2022, true);
const workbench = parsed.statements.find(statement => statement.name?.text === "CanvasDirectorWorkbench");
const declaration = workbench.body.statements.flatMap(statement => statement.declarationList?.declarations || [])
    .find(item => item.name?.text === "handleActorRigReady");
assert.ok(declaration, "真实导演台骨骼回调必须存在");
const javascript = ts.transpileModule(`const ${declaration.getText(parsed)};`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;

function setup(objects) {
    const draftRef = { current: { id: "scene", objects, updatedAt: "2026-10-01" } };
    let saves = 0;
    let next = 0;
    const context = vm.createContext({
        draftRef,
        useCallback: callback => callback,
        nanoid: () => `clip-${++next}`,
        replaceWithoutHistory: update => {
            saves++;
            draftRef.current = { ...update(draftRef.current), updatedAt: "2026-10-09" };
        },
    });
    vm.runInContext(`${javascript}\nthis.ready = handleActorRigReady;`, context);
    return { ready: context.ready, scene: () => draftRef.current, saves: () => saves };
}

const rig = { status: "ready", boneMap: { hips: "Hips", head: "Head" }, animationNames: [] };
const actor = overrides => ({ id: "actor", name: "演员", rig, motionClips: [], ...overrides });

test("首次骨骼映射保存，并生成可用Clip且保留演员编辑", () => {
    const state = setup([actor({ rig: { status: "unmapped", boneMap: {}, animationNames: [] }, pose: "walk" })]);
    state.ready("actor", { ...rig, animationNames: ["走路"] }, [{ name: "走路", duration: 2 }]);
    assert.equal(state.saves(), 1);
    const result = state.scene().objects[0];
    assert.equal(result.pose, "walk");
    assert.equal(result.rig.status, "ready");
    assert.equal(result.motionClips.length, 1);
    assert.equal(result.motionClips[0].sourceAnimation, "走路");
    assert.equal(result.motionClips[0].duration, 2);
});

test("相同骨骼重放保持场景版本与对象，MySQL键序不同也不重新保存", () => {
    const state = setup([actor()]);
    const original = state.scene();
    state.ready("actor", { status: "ready", boneMap: { head: "Head", hips: "Hips" }, animationNames: [] }, []);
    assert.equal(state.saves(), 0);
    assert.equal(state.scene(), original);
    assert.equal(state.scene().updatedAt, "2026-10-01");
});

test("有已编辑Clip的相同骨骼重放保留速度、时间与稳定Clip ID", () => {
    const clip = { id: "saved-clip", name: "已编辑动作", start: 3, duration: 4, playbackRate: 0.5 };
    const state = setup([actor({ motionClips: [clip] })]);
    const original = state.scene();
    state.ready("actor", rig, [{ name: "其他动作", duration: 2 }]);
    assert.equal(state.saves(), 0);
    assert.equal(state.scene(), original);
    assert.equal(state.scene().objects[0].motionClips[0], clip);
});

test("真正变化的骨骼更新保存，并保留已有Clip", () => {
    const clip = { id: "saved-clip", start: 3, playbackRate: 0.5 };
    const state = setup([actor({ motionClips: [clip] })]);
    state.ready("actor", { ...rig, boneMap: { hips: "NewHips", head: "Head" } }, []);
    assert.equal(state.saves(), 1);
    assert.equal(state.scene().objects[0].rig.boneMap.hips, "NewHips");
    assert.equal(state.scene().objects[0].motionClips[0], clip);
});

test("演员删除后的旧模型回调不推进场景版本或触发保存", () => {
    const state = setup([]);
    const original = state.scene();
    state.ready("actor", rig, []);
    assert.equal(state.saves(), 0);
    assert.equal(state.scene(), original);
});
