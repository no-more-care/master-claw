from enum import StrEnum

from masterclaw.app.fiction_context import (
    FictionContextChangedError as FictionContextChangedError,
)
from masterclaw.app.fiction_context import (
    FictionContextSnapshot as FictionContextSnapshot,
)


class WorldWorkspaceStage(StrEnum):
    COLLECTING = "collecting"
    REVIEW = "review"
