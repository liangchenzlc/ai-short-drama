import type { ServiceType } from "./config-model";

export interface ProviderPreset {
  id: string;
  label: string;
  baseUrl: string;
  models: string[];
}

export const providerPresets: Record<ServiceType, ProviderPreset[]> = {
  text: [
    {
      id: "openai",
      label: "OpenAI",
      baseUrl: "https://api.openai.com/v1",
      models: ["gpt-4o", "gpt-4"],
    },
    {
      id: "volcengine",
      label: "火山引擎",
      baseUrl: "https://ark.cn-beijing.volces.com/api/v3",
      models: ["deepseek-v3-2-251201", "doubao-1-5-pro-32k-250115"],
    },
    {
      id: "gemini",
      label: "Google Gemini",
      baseUrl: "https://generativelanguage.googleapis.com",
      models: ["gemini-2.5-pro", "gemini-3-flash-preview"],
    },
    {
      id: "deepseek",
      label: "DeepSeek",
      baseUrl: "https://api.deepseek.com",
      models: ["deepseek-v4-flash", "deepseek-v4-pro"],
    },
    {
      id: "qwen",
      label: "通义千问",
      baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      models: ["qwen3-max", "qwen-plus"],
    },
  ],
  image: [
    {
      id: "volcengine",
      label: "火山引擎",
      baseUrl: "https://ark.cn-beijing.volces.com/api/v3",
      models: ["doubao-seedream-4-5-251128", "doubao-seedream-4-0-250828"],
    },
    {
      id: "gemini",
      label: "Google Gemini",
      baseUrl: "https://generativelanguage.googleapis.com",
      models: ["gemini-2.5-flash-image", "gemini-3-pro-image-preview"],
    },
    {
      id: "openai",
      label: "OpenAI",
      baseUrl: "https://api.openai.com/v1",
      models: ["dall-e-3", "dall-e-2"],
    },
    {
      id: "dashscope",
      label: "通义万象",
      baseUrl: "https://dashscope.aliyuncs.com",
      models: ["wan2.6-image", "qwen-image-edit-plus"],
    },
  ],
  video: [
    {
      id: "kling",
      label: "可灵 Kling",
      baseUrl: "",
      models: ["kling-omni-video", "kling-video"],
    },
    {
      id: "vidu",
      label: "Vidu",
      baseUrl: "",
      models: ["viduq2", "viduq3-pro"],
    },
    {
      id: "volces",
      label: "火山引擎",
      baseUrl: "https://ark.cn-beijing.volces.com/api/v3",
      models: ["doubao-seedance-2-0-260128"],
    },
    {
      id: "minimax",
      label: "MiniMax 海螺",
      baseUrl: "https://api.minimaxi.com/v1",
      models: ["MiniMax-Hailuo-2.3"],
    },
    {
      id: "openai",
      label: "OpenAI",
      baseUrl: "https://api.openai.com/v1",
      models: ["sora-2", "sora-2-pro"],
    },
  ],
};

export function applyPreset(preset: ProviderPreset) {
  return { provider: preset.label, baseUrl: preset.baseUrl, modelKey: preset.models[0] ?? '' };
}
