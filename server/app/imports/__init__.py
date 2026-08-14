"""Bulk import of a tournament's divisions, teams and players from a sheet.

Three layers, deliberately separated so the interesting parts need no database:

* `sheet`    — bytes (.csv or .xlsx) -> normalised rows. Knows about encodings,
               delimiters and header spelling; knows nothing about pickleball.
* `plan`     — rows -> an `ImportPlan` plus a list of `Problem`s. Pure, and
               where every rule about what a valid entry sheet means lives.
* `template` — the blank sheet handed to the organizer, generated from the same
               column definitions the parser accepts, so the two cannot drift.

Applying a plan to the database is `app.services.import_service`.
"""

from app.imports.plan import (
    ImportPlan,
    PlannedDivision,
    PlannedEntry,
    PlannedPlayer,
    Problem,
    build_plan,
    name_key,
)
from app.imports.sheet import COLUMNS, SheetError, read_sheet
from app.imports.template import SAMPLE_ROWS, template_csv, template_xlsx

__all__ = [
    "COLUMNS",
    "ImportPlan",
    "PlannedDivision",
    "PlannedEntry",
    "PlannedPlayer",
    "Problem",
    "SAMPLE_ROWS",
    "SheetError",
    "build_plan",
    "name_key",
    "read_sheet",
    "template_csv",
    "template_xlsx",
]
