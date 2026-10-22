from .ai_model_config import AIModelConfigCreate, AIModelConfigRead, AIModelConfigUpdate
from .asset import AssetCreate, AssetRead, AssetUpdate
from .media_file import MediaFileCreate, MediaFileRead, MediaFileUpdate
from .media_recycle_bin import MediaRecycleBinCreate, MediaRecycleBinRead
from .novel_script_record import NovelScriptRecordCreate, NovelScriptRecordRead
from .project import ProjectCreate, ProjectRead, ProjectUpdate
from .project_novel import ProjectNovelCreate, ProjectNovelRead, ProjectNovelUpdate
from .project_script import ProjectScriptCreate, ProjectScriptRead, ProjectScriptUpdate
from .script_shot_record import ScriptShotRecordCreate, ScriptShotRecordRead
from .shot_asset import ShotAssetCreate, ShotAssetRead, ShotAssetUpdate
from .shot_image import ShotImageCreate, ShotImageRead, ShotImageUpdate
from .shot_script import ShotScriptCreate, ShotScriptRead, ShotScriptUpdate
from .shot_video import ShotVideoCreate, ShotVideoRead, ShotVideoUpdate

__all__ = [
    "ProjectCreate",
    "ProjectUpdate",
    "ProjectRead",
    "AIModelConfigCreate",
    "AIModelConfigUpdate",
    "AIModelConfigRead",
    "MediaFileCreate",
    "MediaFileUpdate",
    "MediaFileRead",
    "ProjectNovelCreate",
    "ProjectNovelUpdate",
    "ProjectNovelRead",
    "ProjectScriptCreate",
    "ProjectScriptUpdate",
    "ProjectScriptRead",
    "AssetCreate",
    "AssetUpdate",
    "AssetRead",
    "ShotScriptCreate",
    "ShotScriptUpdate",
    "ShotScriptRead",
    "ShotAssetCreate",
    "ShotAssetUpdate",
    "ShotAssetRead",
    "ShotImageCreate",
    "ShotImageUpdate",
    "ShotImageRead",
    "ShotVideoCreate",
    "ShotVideoUpdate",
    "ShotVideoRead",
    "ScriptShotRecordCreate",
    "ScriptShotRecordRead",
    "MediaRecycleBinCreate",
    "MediaRecycleBinRead",
    "NovelScriptRecordCreate",
    "NovelScriptRecordRead",
]
