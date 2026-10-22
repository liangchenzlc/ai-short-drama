-- 可信业务来源；不回填 JSON。部分完成或重复升级使用 canvas_migration.py --apply。
ALTER TABLE project_canvases ADD COLUMN source_project_id BIGINT UNSIGNED;

CREATE INDEX idx_canvas_source_project ON project_canvases (source_project_id);

ALTER TABLE project_canvases ADD CONSTRAINT fk_canvas_source_project FOREIGN KEY(source_project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT;
