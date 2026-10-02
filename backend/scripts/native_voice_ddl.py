"""Additive native voice tables; sound_ddl and existing generation migrations must precede this."""

import sys

from short_drama.core.config import Settings
from short_drama.db.session import build_engine
from short_drama.domain.native_voice import CharacterVoice, ProjectSoundMode, ShotDialogue

try:
    from scripts.assembly_ddl import tables_ddl
except ModuleNotFoundError:
    from assembly_ddl import tables_ddl

MODELS = (ProjectSoundMode, CharacterVoice, ShotDialogue)


def ddl():
    return tables_ddl(MODELS) + "\n"


def apply(engine):
    with engine.begin() as connection:
        for model in MODELS:
            model.__table__.create(connection, checkfirst=True)


if __name__ == "__main__":
    if "--apply" in sys.argv:
        engine = build_engine(Settings())
        try:
            apply(engine)
            print("Native voice tables ready; existing projects retain legacy mode.")
        finally:
            engine.dispose()
    else:
        print(ddl(), end="")
