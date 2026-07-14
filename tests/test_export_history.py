"""Standalone check for app.py's select_export_components() -- the
validation/filtering logic behind GET /api/export/history.

Plain asserts, no test framework. Run directly: python tests/test_export_history.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import select_export_components, _EXPORTABLE_COMPONENTS


# Omitted entirely -> None, meaning "export everything" (the caller expands
# this to the full component set).
assert select_export_components(None) is None
assert select_export_components("") is None

# A valid subset, in any case/spacing, comes back as the matching set.
assert select_export_components("gpu,cpu") == {"gpu", "cpu"}
assert select_export_components(" GPU , Cpu ,memory ") == {"gpu", "cpu", "memory"}

# A mix of valid and garbage keeps only the valid ones.
assert select_export_components("gpu,not-a-real-component") == {"gpu"}

# All garbage -> empty set (distinct from None/"omitted") so the endpoint
# can reject it as a 400 instead of silently exporting nothing.
assert select_export_components("not-a-real-component,also-fake") == set()

# Every declared component is independently selectable.
for name in _EXPORTABLE_COMPONENTS:
    assert select_export_components(name) == {name}, f"{name!r} did not select itself"

print("tests/test_export_history.py passed")
