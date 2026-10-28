CREATE TABLE project_story_outlines (
	id BIGINT UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	content MEDIUMTEXT NOT NULL COMMENT '当前故事大纲正文' DEFAULT (''), 
	created_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6), 
	updated_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6), 
	created_by BIGINT UNSIGNED, 
	updated_by BIGINT UNSIGNED, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_story_outlines_project UNIQUE (project_id), 
	CONSTRAINT fk_story_outlines_project FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT ck_story_outlines_audit_time CHECK (`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`)
)ENGINE=InnoDB ROW_FORMAT=DYNAMIC CHARSET=utf8mb4 COMMENT='故事大纲；每个作品一份当前正文' COLLATE utf8mb4_0900_ai_ci;
