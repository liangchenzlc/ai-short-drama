import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { Euler, Matrix4, Quaternion, Vector3 } from "three";

// 运行原数学函数，与同版渲染器使用的Three类对照；不替换坐标或旋转算法。
const root = fileURLToPath(new URL("../..", import.meta.url));
const bundled = await build({
    stdin: { contents: 'export * from "@/lib/canvas/director/director-scene"; export * from "@/lib/canvas/director/director-stage-transform";', resolveDir: root, loader: "ts" },
    alias: { "@": fileURLToPath(new URL("../../src", import.meta.url)) },
    bundle: true, write: false, format: "esm", platform: "node", metafile: true,
});
const math = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`);

const stages = [
    { scale: 1, position: [0, 0, 0], rotation: [0, 0, 0] },
    { scale: 0.1, position: [-20, 0.25, 4], rotation: [90, -180, 270] },
    { scale: 10, position: [10, -4, 30], rotation: [-23.5, 77.25, 12] },
    { scale: 2.75, position: [0.125, 12, -3.5], rotation: [0, 90, 0] },
];
const transform = { position: [2.25, -1, 8], rotation: [0.8, -1.2, 0.25], scale: [1.5, 0.5, 2] };

function rendererStage(stage) {
    const euler = new Euler(...stage.rotation.map(value => value * Math.PI / 180));
    return new Matrix4().compose(new Vector3(...stage.position), new Quaternion().setFromEuler(euler), new Vector3(stage.scale, stage.scale, stage.scale));
}

test("首屏导演数学依赖不引入Three完整渲染入口", () => {
    const rendererEntries = Object.keys(bundled.metafile.inputs).filter(path => /three[/\\]build[/\\]three\.(?:core|module)\.js$/.test(path));
    assert.deepEqual(rendererEntries, []);
});

test("舞台矩阵与渲染器互操作，点、方向和相机转换保持原数值", () => {
    for (const stage of stages) {
        const expected = rendererStage(stage);
        const actual = math.directorStageMatrix(stage);
        assert.deepEqual(actual.elements, expected.elements);
        assert.deepEqual(new Vector3(...transform.position).applyMatrix4(actual).toArray(), new Vector3(...transform.position).applyMatrix4(expected).toArray());
        assert.deepEqual(math.directorStagePoint(stage, transform.position), new Vector3(...transform.position).applyMatrix4(expected).toArray());
        assert.deepEqual(math.directorStageLocalPoint(stage, transform.position), new Vector3(...transform.position).applyMatrix4(expected.clone().invert()).toArray());
        assert.deepEqual(math.directorStageDirection(stage, transform.position), new Vector3(...transform.position).transformDirection(expected).toArray());
        const local = math.directorStageLocalCamera(stage, transform);
        if (stage === stages[0]) assert.equal(local, transform);
        else {
            const worldRotation = new Quaternion().setFromEuler(new Euler(...transform.rotation));
            const stageRotation = new Quaternion().setFromEuler(new Euler(...stage.rotation.map(value => value * Math.PI / 180)));
            const rotation = new Euler().setFromQuaternion(stageRotation.invert().multiply(worldRotation));
            assert.deepEqual(local, { ...transform, position: new Vector3(...transform.position).applyMatrix4(expected.clone().invert()).toArray(), rotation: [rotation.x, rotation.y, rotation.z] });
        }
    }
});

test("旋转和骨骼关键帧插值保留同版Quaternion结果与输入", () => {
    const next = { position: [-4, 10, 0.5], rotation: [-1.7, 1.1, 2.5], scale: [0.25, 3, 1] };
    const frames = [{ id: "start", time: 0, transform }, { id: "end", time: 4, transform: next }];
    const bones = frames.map(frame => ({ id: frame.id, time: frame.time, rotation: new Quaternion().setFromEuler(new Euler(...frame.transform.rotation)).toArray() }));
    const before = structuredClone({ frames, bones });
    for (const time of [0, 0.5, 2, 3.99, 4]) {
        const progress = time / 4;
        const rotation = new Quaternion(...bones[0].rotation).slerp(new Quaternion(...bones[1].rotation), progress);
        const expected = math.interpolateDirectorTransform(transform, frames, time);
        if (time > 0 && time < 4) assert.deepEqual(expected.rotation, new Euler().setFromQuaternion(rotation).toArray().slice(0, 3));
        assert.deepEqual(math.interpolateDirectorBoneRotation([0, 0, 0, 1], bones, time), time === 0 ? bones[0].rotation : time === 4 ? bones[1].rotation : rotation.toArray());
    }
    assert.deepEqual({ frames, bones }, before);
});


test("提示词颜色标签保持同版颜色空间的原结果", () => {
    for (const [color, label] of [["#ffffff", "白色"], ["#000000", "黑色"], ["#ff0000", "红色"], ["#00ff00", "绿色"], ["#0000ff", "蓝色"], ["#ffff00", "黄色"], ["#00ffff", "青色"], ["#ff00ff", "粉色"]]) {
        assert.equal(math.directorColorLabel(color), label);
    }
});
