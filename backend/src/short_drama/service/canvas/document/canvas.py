from short_drama.service.canvas.document.commits import CanvasCommits
from short_drama.service.canvas.document.creation import CanvasCreation
from short_drama.service.canvas.document.preferences import CanvasPreferences
from short_drama.service.canvas.document.reading import CanvasReading
from short_drama.service.canvas.document.revisions import CanvasRevisions


class CanvasService(
    CanvasReading, CanvasCreation, CanvasCommits, CanvasRevisions, CanvasPreferences
):
    """组合独立职责；共享同一会话，事务仍由具体操作完整持有。"""
