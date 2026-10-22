-- Explicit offline migration for the account assistant.

ALTER TABLE character_voices MODIFY COLUMN record_id BIGINT UNSIGNED NULL;

ALTER TABLE character_voices MODIFY COLUMN media_id BIGINT UNSIGNED NULL;

ALTER TABLE character_voices ADD CONSTRAINT ck_character_voice_binding_pair CHECK ((record_id IS NULL AND media_id IS NULL) OR (record_id IS NOT NULL AND media_id IS NOT NULL));
