"""Compatibility exports; workspace discovery is shared by analysis and inference."""

from smelt.analysis.workspace import (
    WorkspaceMember as WorkspaceMember,  # noqa: PLC0414 - compatibility re-export
)
from smelt.analysis.workspace import (
    import_root as import_root,  # noqa: PLC0414 - compatibility re-export
)
from smelt.analysis.workspace import (
    workspace_members as workspace_members,  # noqa: PLC0414 - compatibility re-export
)
