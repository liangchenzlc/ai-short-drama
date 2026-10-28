CREATE TABLE project_script_revisions (
	id BIGINT UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	script_id BIGINT UNSIGNED NOT NULL, 
	before_version BIGINT UNSIGNED NOT NULL, 
	after_version BIGINT UNSIGNED NOT NULL, 
	source VARCHAR(24) NOT NULL, 
	before_content MEDIUMTEXT NOT NULL, 
	after_content MEDIUMTEXT NOT NULL, 
	before_document JSON, 
	after_document JSON, 
	created_by BIGINT UNSIGNED, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_script_revisions_project FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_script_revisions_script FOREIGN KEY(script_id) REFERENCES project_scripts (id) ON DELETE RESTRICT, 
	CONSTRAINT uk_script_revisions_version UNIQUE (project_id, after_version), 
	CONSTRAINT ck_script_revisions_version CHECK (after_version > before_version)
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;
