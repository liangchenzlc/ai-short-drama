from short_drama.domain import EpisodeScript, ScriptShotRecord, ShotScript
from short_drama.schemas.script_shot_record import ScriptShotRecordCreate, ScriptShotRecordRead

from .novel_script_record_service import GenerationRecordService


class ScriptShotRecordService(GenerationRecordService):
    model = ScriptShotRecord
    create_schema = ScriptShotRecordCreate
    read_schema = ScriptShotRecordRead
    source_model = EpisodeScript
    output_model = ShotScript
    source_field = "script_id"
    output_field = "shot_id"
