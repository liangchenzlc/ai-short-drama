import { Canvas, useLoader, useThree } from "@react-three/fiber";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Suspense, useEffect, useMemo, useRef } from "react";
import { Box3, Color, Mesh, MeshStandardMaterial, Vector3, type Object3D } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { KTX2Loader } from "three/examples/jsm/loaders/KTX2Loader.js";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";
import { aceternityMotion } from "@/lib/aceternity-motion";
import { canvasThemes } from "@/lib/canvas-theme";
import { emotionBlendshapes, type CanvasEmotionPreset } from "@/lib/canvas/canvas-emotion";
import { useActiveTheme } from "@/stores/canvas/use-canvas-theme-store";

export function EmotionHeadPreview({ preset }: { preset: CanvasEmotionPreset }) {
    const theme = canvasThemes[useActiveTheme()];
    return (
        <div className="relative overflow-hidden rounded-[var(--r-lg)] border" style={{ background: "#26272a", borderColor: theme.toolbar.border }}>
            <Canvas frameloop="demand" dpr={[1, 1.5]} camera={{ fov: 38, near: 0.1, far: 20, position: [0, 0, 4.15] }} gl={{ antialias: true, alpha: false, powerPreference: "high-performance" }}>
                <color attach="background" args={["#26272a"]} />
                <ambientLight intensity={0.82} />
                <directionalLight position={[-2.8, 4, 3]} intensity={1.45} color="#ffffff" />
                <directionalLight position={[3, 1, 2]} intensity={0.5} color="#c9d0dc" />
                <Suspense fallback={null}><EmotionFaceModel preset={preset} /></Suspense>
            </Canvas>
            <div className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-black/55 to-transparent" />
            <AnimatePresence mode="wait" initial={false}>
                <motion.span key={preset.id} initial={{ opacity: 0, filter: "blur(6px)" }} animate={{ opacity: 1, filter: "blur(0px)" }} exit={{ opacity: 0, filter: "blur(5px)" }} transition={{ duration: aceternityMotion.duration.state }} className="pointer-events-none absolute bottom-2 left-2.5 text-[var(--fs-tiny)] font-medium text-white/72">实时预览 · {preset.label}</motion.span>
            </AnimatePresence>
        </div>
    );
}

function EmotionFaceModel({ preset }: { preset: CanvasEmotionPreset }) {
    const renderer = useThree((state) => state.gl);
    const gltf = useLoader(GLTFLoader, "/canvas/models/facecap.glb", (loader) => {
        loader.setKTX2Loader(new KTX2Loader().setTranscoderPath("/three/basis/").detectSupport(renderer));
        loader.setMeshoptDecoder(MeshoptDecoder);
    });
    const invalidate = useThree((state) => state.invalidate);
    const reducedMotion = useReducedMotion();
    const model = useMemo(() => createMannequinModel(gltf.scene), [gltf.scene]);
    const animationRef = useRef<number | null>(null);

    useEffect(() => {
        const targets = emotionBlendshapes(preset);
        const meshes = morphMeshes(model);
        const starts = meshes.map((mesh) => [...(mesh.morphTargetInfluences || [])]);
        const startTime = performance.now();
        const duration = reducedMotion ? 0 : 220;
        const animate = (now: number) => {
            const progress = duration ? Math.min(1, (now - startTime) / duration) : 1;
            const eased = 1 - Math.pow(1 - progress, 3);
            meshes.forEach((mesh, meshIndex) => {
                const dictionary = mesh.morphTargetDictionary || {};
                const influences = mesh.morphTargetInfluences || [];
                Object.entries(dictionary).forEach(([name, index]) => {
                    const target = targets[name as keyof typeof targets] || 0;
                    influences[index] = (starts[meshIndex][index] || 0) + (target - (starts[meshIndex][index] || 0)) * eased;
                });
            });
            invalidate();
            if (progress < 1) animationRef.current = requestAnimationFrame(animate);
        };
        if (animationRef.current) cancelAnimationFrame(animationRef.current);
        animationRef.current = requestAnimationFrame(animate);
        return () => { if (animationRef.current) cancelAnimationFrame(animationRef.current); };
    }, [invalidate, model, preset, reducedMotion]);

    return <primitive object={model} />;
}

function createMannequinModel(source: Object3D) {
    const model = source.clone(true);
    model.traverse((object) => {
        if (!(object instanceof Mesh)) return;
        const material = new MeshStandardMaterial({ color: new Color("#aaacae"), roughness: 0.9, metalness: 0.01 });
        object.material = material;
        object.castShadow = false;
        object.receiveShadow = false;
    });
    // 不依赖模型原点，按真实包围盒归一化后再居中，避免不同导出版本出现头像偏移。
    const bounds = new Box3().setFromObject(model);
    const size = bounds.getSize(new Vector3());
    model.scale.multiplyScalar(2.35 / Math.max(size.y, 0.001));
    const normalizedBounds = new Box3().setFromObject(model);
    model.position.sub(normalizedBounds.getCenter(new Vector3()));
    return model;
}

function morphMeshes(model: Object3D) {
    const meshes: Mesh[] = [];
    model.traverse((object) => {
        if (object instanceof Mesh && object.morphTargetDictionary && object.morphTargetInfluences) meshes.push(object);
    });
    return meshes;
}
