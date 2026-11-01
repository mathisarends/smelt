from pathlib import Path

import pytest

from smelt.analysis.parsing import AnalysisError
from smelt.engine.source_fixes import SourceEdit
from tests.helpers import check, write_project


def test_edit_between_preparation_and_write_is_preserved(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\n",
            "app/service.py": "value = 1\n",
        },
    )
    outcome = check(root)
    edit = SourceEdit.prepare(
        outcome.context, "app/service.py", lambda text: text.replace("1", "2")
    )
    target = root / "app/service.py"
    target.write_text("value = 3\n")
    with pytest.raises(AnalysisError, match="changed since fix preparation"):
        edit.write(root)
    assert target.read_text() == "value = 3\n"
