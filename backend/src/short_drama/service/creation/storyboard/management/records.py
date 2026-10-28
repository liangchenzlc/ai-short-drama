from short_drama.domain import ProjectScript, ScriptShotRecord, ShotScript
from short_drama.schemas.creation.storyboard.management.records import (
    ScriptShotRecordCreate,
    ScriptShotRecordRead,
)
from short_drama.service.generations.results.records import GenerationRecordService


class ScriptShotRecordService(GenerationRecordService):
    model = ScriptShotRecord
    create_schema = ScriptShotRecordCreate
    read_schema = ScriptShotRecordRead
    source_model = ProjectScript
    output_model = ShotScript
    source_field = "script_id"
    output_field = "shot_id"
