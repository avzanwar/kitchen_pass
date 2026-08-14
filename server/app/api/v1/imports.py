"""Bulk import: upload a spreadsheet, preview it, then commit it.

Registering a 40-team event by hand is the single most tedious thing an
organizer does, and it is exactly the job a spreadsheet already does well —
most of them arrive with one. So the sheet is the input format, and the app's
job is to read it honestly and say what it found before touching anything.

Two endpoints do the work and they share a parse: `/preview` reports, `/commit`
writes. The commit re-validates from the file rather than trusting a preview,
so a stale preview can never smuggle in a plan the rules would now reject.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from app.api.deps import CurrentUser, SessionDep
from app.imports import ImportPlan, SheetError, build_plan, name_key, read_sheet
from app.imports.template import template_csv, template_xlsx
from app.models import Tournament
from app.schemas import (
    ImportDivisionOut,
    ImportEntryOut,
    ImportPlayerOut,
    ImportPreviewOut,
    ImportProblemOut,
    ImportResultOut,
    TournamentOut,
)
from app.services.import_service import ImportRejected, apply, resolve

router = APIRouter(prefix="/imports", tags=["imports"])

#: Generous for a sheet of names, small enough that a mis-picked video or a
#: photo library export is rejected before it is read into memory.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
#
# Deliberately unauthenticated. They are generated sample files containing no
# user data, and the browser downloads them through a plain link, which cannot
# carry the bearer token the rest of the API needs.


@router.get("/template.csv", include_in_schema=True)
async def download_template_csv() -> Response:
    return Response(
        content=template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                'attachment; filename="kitchen-pass-teams-template.csv"'
        },
    )


@router.get("/template.xlsx", include_in_schema=True)
async def download_template_xlsx() -> Response:
    return Response(
        content=template_xlsx(),
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition":
                'attachment; filename="kitchen-pass-teams-template.xlsx"'
        },
    )


# ---------------------------------------------------------------------------
# Preview and commit
# ---------------------------------------------------------------------------


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"That file is larger than "
                   f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )
    if not data:
        raise HTTPException(status_code=422, detail="The uploaded file is empty")
    return data


async def _target(
    session: SessionDep, user: CurrentUser, tournament_id: str | None
) -> Tournament | None:
    if not tournament_id:
        return None
    tournament = await session.get(Tournament, tournament_id)
    if tournament is None or tournament.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Tournament not found")
    return tournament


def _plan_from(data: bytes, filename: str) -> ImportPlan:
    try:
        sheet = read_sheet(data, filename)
    except SheetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return build_plan(sheet)


def _preview_out(
    plan: ImportPlan, tournament: Tournament | None, tournament_name: str
) -> ImportPreviewOut:
    # Count distinct people, not rows: someone entered in three divisions is one
    # roster addition, which is what the organizer is being asked to sanity-check.
    known: dict[str, bool] = {}
    for division in plan.divisions:
        for entry in division.entries:
            for player in entry.players:
                key = name_key(player.name)
                known[key] = known.get(key, False) or player.existing_id is not None
    matched = sum(1 for is_known in known.values() if is_known)
    new_players = len(known) - matched

    return ImportPreviewOut(
        ok=plan.ok,
        tournament_name=tournament.name if tournament else tournament_name,
        tournament_id=tournament.id if tournament else None,
        creates_tournament=tournament is None,
        divisions=[
            ImportDivisionOut(
                name=d.name,
                format=d.format,
                draw_kind=d.draw_kind,
                skill=d.skill,
                age=d.age,
                best_of=d.best_of,
                pools=d.pools,
                existing=d.existing_id is not None,
                entries=[
                    ImportEntryOut(
                        row=e.row,
                        name=e.name,
                        seed=e.seed,
                        players=[
                            ImportPlayerOut(
                                name=p.name,
                                rating=p.rating,
                                existing=p.existing_id is not None,
                            )
                            for p in e.players
                        ],
                    )
                    for e in d.entries
                ],
            )
            for d in plan.divisions
        ],
        problems=[
            ImportProblemOut(severity=p.severity, message=p.message, row=p.row)
            for p in plan.problems
        ],
        entry_count=plan.entry_count,
        new_players=new_players,
        matched_players=matched,
    )


@router.post("/preview", response_model=ImportPreviewOut)
async def preview_import(
    session: SessionDep,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    tournament_id: Annotated[str | None, Form()] = None,
    tournament_name: Annotated[str, Form()] = "",
) -> ImportPreviewOut:
    """Parse an upload and report what it would create. Writes nothing."""
    data = await _read_upload(file)
    tournament = await _target(session, user, tournament_id)
    plan = _plan_from(data, file.filename or "")
    plan = await resolve(session, plan, user, tournament)
    return _preview_out(plan, tournament, tournament_name)


@router.post("/commit", response_model=ImportResultOut, status_code=201)
async def commit_import(
    session: SessionDep,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    tournament_id: Annotated[str | None, Form()] = None,
    tournament_name: Annotated[str, Form()] = "",
) -> ImportResultOut:
    """Create everything the sheet describes, or nothing at all."""
    data = await _read_upload(file)
    tournament = await _target(session, user, tournament_id)
    plan = _plan_from(data, file.filename or "")

    if tournament is None and not tournament_name.strip():
        raise HTTPException(
            status_code=422,
            detail="Name the new tournament, or choose one to import into",
        )

    try:
        outcome = await apply(
            session,
            plan,
            user,
            tournament=tournament,
            tournament_name=tournament_name,
        )
    except ImportRejected as exc:
        # 422 with the full preview attached, so the UI can show the same
        # row-by-row problem list it shows before the commit.
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "preview": _preview_out(
                    exc.plan, tournament, tournament_name
                ).model_dump(),
            },
        ) from exc

    return ImportResultOut(
        tournament=TournamentOut.model_validate(outcome.tournament),
        tournament_created=outcome.tournament_created,
        divisions_created=outcome.divisions_created,
        divisions_reused=outcome.divisions_reused,
        entries_created=outcome.entries_created,
        players_created=outcome.players_created,
        players_matched=outcome.players_matched,
        problems=[
            ImportProblemOut(severity=p.severity, message=p.message, row=p.row)
            for p in plan.problems
        ],
    )
