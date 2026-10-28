from short_drama.service.creation.sound.tracks.adoption import AdoptionOperations
from short_drama.service.creation.sound.tracks.editing import EditingOperations
from short_drama.service.creation.sound.tracks.generation import GenerationOperations
from short_drama.service.creation.sound.tracks.reading import ReadingOperations
from short_drama.service.creation.sound.tracks.snapshot import (
    line_hash as line_hash,
)
from short_drama.service.creation.sound.tracks.snapshot import (
    sound_duration as sound_duration,
)
from short_drama.service.creation.sound.tracks.snapshot import (
    validate_sound_snapshot as validate_sound_snapshot,
)


class ProjectSoundService(
    ReadingOperations, EditingOperations, GenerationOperations, AdoptionOperations
):
    """组合独立职责，共享同一 Session；事务仍由原业务操作完整持有。"""
