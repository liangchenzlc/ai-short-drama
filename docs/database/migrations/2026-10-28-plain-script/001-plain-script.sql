ALTER TABLE `project_scripts`
  DROP CHECK `ck_scripts_editor_document`,
  DROP COLUMN `editor_document`;

ALTER TABLE `project_script_revisions`
  DROP COLUMN `before_document`,
  DROP COLUMN `after_document`;
