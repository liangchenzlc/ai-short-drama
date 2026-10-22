-- 在已完成时间轴与声音升级的旧库执行。保留所有片段、声音与历史任务。
ALTER TABLE episode_assembly_clips
  MODIFY COLUMN shot_id BIGINT UNSIGNED NULL,
  ADD COLUMN source_type VARCHAR(8) NOT NULL DEFAULT 'shot' AFTER shot_id,
  ADD COLUMN display_duration_ms BIGINT UNSIGNED NULL AFTER source_type,
  ADD COLUMN transform JSON NULL AFTER display_duration_ms,
  ADD CONSTRAINT ck_assembly_clips_source CHECK (source_type IN ('shot','video','image')),
  ADD CONSTRAINT ck_assembly_clips_transform CHECK (transform IS NULL OR JSON_TYPE(transform) = 'OBJECT');

ALTER TABLE episode_render_jobs
  DROP CHECK ck_render_jobs_kind,
  ADD CONSTRAINT ck_render_jobs_kind CHECK (kind IN ('probe','export','preview','transcription','audio_analysis'));
