export type Attempt = {
  key: string;
  body: {
    row_version: string;
    clip_id?: string;
    language: string;
    model_config_id?: string;
  };
};
