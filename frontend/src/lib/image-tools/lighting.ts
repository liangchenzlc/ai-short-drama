export type CanvasImageLightingOptions = {
    azimuth: number;
    elevation: number;
    brightness: number;
    rimLight: boolean;
    stylePreset: string;
    smartMode: boolean;
    lightColor: string;
};

export const defaultLightingOptions: CanvasImageLightingOptions = { azimuth: 0, elevation: 0, brightness: 50, rimLight: false, stylePreset: "", smartMode: true, lightColor: "#ffffff" };

export const LIGHT_POSITIONS = [
    { label: "左侧", azimuth: 270, elevation: 0 },
    { label: "顶部", azimuth: 0, elevation: 80 },
    { label: "右侧", azimuth: 90, elevation: 0 },
    { label: "前方", azimuth: 0, elevation: 0 },
    { label: "底部", azimuth: 0, elevation: -80 },
    { label: "后方", azimuth: 180, elevation: 0 },
];

export const LIGHTING_PREVIEW_COLUMN_WIDTH = 300;
export const LIGHTING_SPHERE_SIZE = 220;

export const STYLE_PRESETS = [
    { id: "overexposed", name: "过曝胶片", color: "#d4b896", image: "/lighting-presets/overexposed.png", prompt: "overexposed film aesthetic, high-key lighting, washed out highlights, soft diffused light, vintage film look" },
    { id: "blueBacklight", name: "蓝色逆光", color: "#1a3a5c", image: "/lighting-presets/blue-backlight.png", prompt: "dramatic backlighting, blue rim light, cool color temperature, silhouette with colored edges, ethereal atmosphere" },
    { id: "rembrandt", name: "伦勃朗光", color: "#5a3a1a", image: "/lighting-presets/rembrandt.png", prompt: "Rembrandt lighting, 45-degree angle key light, dramatic chiaroscuro, painterly shadows, classical portraiture" },
    { id: "cyberpunk", name: "赛博朋克", color: "#2a0a2a", image: "/lighting-presets/cyberpunk.png", prompt: "cyberpunk neon lighting, synthetic glow, futuristic atmosphere, vibrant cyan and magenta neon" },
    { id: "sunset", name: "落日迷幻", color: "#7a3010", image: "/lighting-presets/sunset.png", prompt: "golden hour lighting, warm sunset tones, long shadow, romantic atmosphere, Kodachrome colors" },
    { id: "mysterious", name: "神秘暗调", color: "#0a0a14", image: "/lighting-presets/mysterious.png", prompt: "low-key noir lighting, deep shadows, mysterious mood, film noir style, high contrast cinematic" },
    { id: "goldenHour", name: "黄金时刻", color: "#7a5a00", image: "/lighting-presets/golden-hour.png", prompt: "golden hour photography, warm soft light, beautiful catchlights, lens flare, magical golden glow" },
    { id: "nolanGrey", name: "诺兰冷灰", color: "#1a2a2a", image: "/lighting-presets/nolan-grey.png", prompt: "Christopher Nolan cinematography, IMAX quality, desaturated cold palette, teal and grey grading" },
];

export const DEFAULT_LIGHTING_PROMPT_TEMPLATE = [
    "{{consistencyPrompt}}",
    "{{presetPrompt}}",
    "{{smartDesc}}",
    "{{lightDirectionPrompt}}",
    "{{brightnessPrompt}}",
    "{{rimLightPrompt}}",
    "{{lightColorPrompt}}",
    "{{lightingMeta}}",
].join(", ");

export const LIGHTING_PROMPT_PLACEHOLDERS = [
    "{{consistencyPrompt}}",
    "{{presetPrompt}}",
    "{{smartDesc}}",
    "{{lightDirectionPrompt}}",
    "{{brightnessPrompt}}",
    "{{rimLightPrompt}}",
    "{{lightColorPrompt}}",
    "{{lightingMeta}}",
];

export function buildLightingLabel(options: CanvasImageLightingOptions) {
    const preset = STYLE_PRESETS.find((item) => item.id === options.stylePreset);
    const position = LIGHT_POSITIONS.find((item) => item.azimuth === options.azimuth && item.elevation === options.elevation);
    const parts: string[] = [];
    parts.push(preset?.name || (position ? `主光${position.label}` : `主光 ${options.azimuth}°/${options.elevation}°`));
    if (options.brightness !== 50) parts.push(`亮度 ${options.brightness}%`);
    if (options.rimLight) parts.push("轮廓光");
    if (options.lightColor && options.lightColor.toLowerCase() !== "#ffffff") parts.push(`光色 ${options.lightColor}`);
    return `AI 打光：${parts.join("，")}`;
}

function applyPromptTemplate(template: string, values: Record<string, string>) {
    return template
        .replace(/\{\{(\w+)\}\}/g, (_match, key: string) => values[key] ?? "")
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean)
        .join(", ");
}

// 将方位角与俯仰角转换成模型更稳定的自然语言；禁止描述实体灯具，避免模型把摄影设备画进画面。
export function buildLightDirectionPrompt(azimuth: number, elevation: number) {
    const azimuthDegrees = ((azimuth % 360) + 360) % 360;
    if (elevation >= 60) return "strong top-down key light from directly above";
    if (elevation <= -60) return "strong upward uplight from directly below the subject";
    const azimuthName = azimuthDegrees >= 345 || azimuthDegrees <= 15
        ? "coming from the front"
        : azimuthDegrees > 15 && azimuthDegrees < 75
            ? "coming from the front-right at 45 degrees"
            : azimuthDegrees >= 75 && azimuthDegrees <= 105
                ? "coming from the right side, pure side-light"
                : azimuthDegrees > 105 && azimuthDegrees < 165
                    ? "coming from the back-right, creating rim and edge light"
                    : azimuthDegrees >= 165 && azimuthDegrees <= 195
                        ? "coming from directly behind the subject, strong backlight and silhouette"
                        : azimuthDegrees > 195 && azimuthDegrees < 255
                            ? "coming from the back-left, creating rim and edge light"
                            : azimuthDegrees >= 255 && azimuthDegrees <= 285
                                ? "coming from the left side, pure side-light"
                                : "coming from the front-left at 45 degrees";
    const elevationName = elevation >= 30
        ? ", tilted downward from above"
        : elevation <= -30
            ? ", tilted upward from below"
            : "";
    return `main key light ${azimuthName}${elevationName}`;
}

export function buildLightingPrompt(input: {
    template: string;
    consistencyPrompt: string;
    presetPrompt: string;
    smartDesc: string;
    lightDirectionPrompt: string;
    brightnessPrompt: string;
    rimLightPrompt: string;
    lightColorPrompt: string;
    lightingMeta: string;
}) {
    const template = input.template.includes("{{consistencyPrompt}}") ? input.template : `{{consistencyPrompt}}, ${input.template}`;
    return applyPromptTemplate(template, {
        consistencyPrompt: input.consistencyPrompt,
        presetPrompt: input.presetPrompt,
        smartDesc: input.smartDesc,
        lightDirectionPrompt: input.lightDirectionPrompt,
        brightnessPrompt: input.brightnessPrompt,
        rimLightPrompt: input.rimLightPrompt,
        lightColorPrompt: input.lightColorPrompt,
        lightingMeta: input.lightingMeta,
    });
}

export function buildImageLightingPrompt(options: CanvasImageLightingOptions, smartDesc = "", template = DEFAULT_LIGHTING_PROMPT_TEMPLATE) {
        const presetPrompt = options.stylePreset ? STYLE_PRESETS.find((preset) => preset.id === options.stylePreset)?.prompt ?? "" : "";
        const smartDescPrompt = options.smartMode ? smartDesc.trim() : "";
        const brightnessPrompt = options.brightness >= 75
            ? "high-key, bright and well-lit scene, lifted exposure"
            : options.brightness <= 25
                ? "low-key, dim and moody scene, deep shadows, reduced exposure"
                : options.brightness === 50
                    ? ""
                    : options.brightness > 50
                        ? "slightly brighter exposure"
                        : "slightly darker exposure";
        const rimLightPrompt = options.rimLight ? "add clear rim light and edge highlight along the silhouette" : "";
        const lightColorPrompt = options.lightColor && options.lightColor.toLowerCase() !== "#ffffff" ? `key light color temperature tinted toward ${options.lightColor}` : "";
        const lightingMeta = `[lighting azimuth:${options.azimuth}° elevation:${options.elevation}° brightness:${options.brightness}%${options.rimLight ? " rim:on" : ""}]`;
        const consistencyPrompt = [
            "this is a lighting-only edit of the reference image: same scene, same people, same action, same clothing, same background, only the lighting changes",
            "the following instructions describe how light falls on the subject, they are not new objects to add to the scene",
            "do not add any lamp, spotlight, light fixture, reflector, softbox, torch, candle, or photography equipment into the image",
            "preserve identity, face, outfit, hairstyle, body pose, and the background layout from the input image",
            "if multiple people are present, preserve the same number of people and their spatial relationship",
            "only change lighting direction, light color, brightness, shadows, contrast, and mood",
        ].join(", ");
        return buildLightingPrompt({
            template,
            consistencyPrompt,
            presetPrompt,
            smartDesc: smartDescPrompt,
            lightDirectionPrompt: buildLightDirectionPrompt(options.azimuth, options.elevation),
            brightnessPrompt,
            rimLightPrompt,
            lightColorPrompt,
            lightingMeta,
        });
}
