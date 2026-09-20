export type ServiceType = "text" | "image" | "video";
export const serviceLabels: Record<ServiceType, string> = {
  text: "文本模型",
  image: "生图模型",
  video: "生视频模型",
};
export interface AiConfig {
  id: string;
  name: string;
  provider: string;
  serviceType: ServiceType;
  baseUrl: string;
  modelKey: string;
  enabled: boolean;
  hasApiKey: boolean;
  rowVersion: string;
  isDefault: boolean;
}
export type ConfigDraft = Omit<AiConfig, "id" | "isDefault" | "hasApiKey" | "rowVersion"> & { apiKey: string; clearApiKey: boolean };
export const emptyConfig: ConfigDraft = {
  name: "",
  provider: "",
  serviceType: "text",
  baseUrl: "",
  modelKey: "",
  enabled: true,
  clearApiKey: false,
  apiKey: "",
};
