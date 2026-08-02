"""Background work, and an honest account of how far along it is.

Preparing a quotation takes forty seconds; three tiers take ninety. Holding the
HTTP request open for that long makes the browser the thing that has to stay
put - navigate away, reload, close the laptop, and the work is lost along with
the Gemini spend. So the request starts a job and returns immediately, and the
job outlives the page that asked for it.

Progress is reported, not simulated. Each job declares the steps it will take
and marks them as they actually finish, so the bar moves when something has
happened rather than on a timer. A bar that fills smoothly while nothing is
happening is a lie told in CSS, and the moment it stalls at 90% nobody believes
any of it again.

Jobs are held in memory and mirrored to disk. The mirror is for reading a
finished job after a restart; anything still running when the process died is
marked failed on the way back up, because a job with nobody working on it is
not going to finish on its own.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Dict, List, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app import hub, inbox, workspaces

logger = logging.getLogger("prism.jobs")

__all__ = ["Job", "JobStep", "create", "get", "listing", "start", "step", "finish", "fail", "restore"]

JOBS_DIRNAME = "_jobs"
ID_LENGTH = 12

State = Literal["queued", "running", "done", "failed"]

_lock = threading.RLock()
#: Jobs in memory, per workspace. A job belongs to the book it was started in,
#: and the page listing them is per workspace too.
_by_workspace: Dict[str, Dict[str, "Job"]] = {}


def _jobs_for() -> Dict[str, "Job"]:
    with _lock:
        return _by_workspace.setdefault(workspaces.current(), {})


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class JobStep(BaseModel):
    """One unit of work the job promised to do."""

    label: str = ""
    done: bool = False


class Job(BaseModel):
    """A piece of work running behind the request that asked for it."""

    id: str
    kind: str = Field(default="quotation", description="quotation | revision")
    title: str = Field(default="", description="What the user recognises it by.")
    detail: str = Field(default="", description="A second line: client, tiers, whatever helps.")
    state: State = "queued"
    stage: str = Field(default="", description="What is happening right now.")
    steps: List[JobStep] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    finished_at: str = ""
    result_ids: List[str] = Field(default_factory=list, description="Quotations this produced.")
    owner: str = Field(
        default="",
        description=(
            "Whose work this is, as an inbox key. Persisted because the restart sweep runs "
            "with no request behind it and still has to tell somebody. Never sent to a client."
        ),
    )
    error: str = Field(default="", description="What went wrong, in the user's terms.")

    def compute_progress(self) -> float:
        """0 to 1, from steps actually finished.

        A queued job sits at 0 and a running job with no completed step yet
        shows a sliver - enough to read as started, not enough to claim ground
        it has not taken.
        """
        if self.state == "done":
            return 1.0
        if not self.steps:
            return 0.0 if self.state == "queued" else 0.08
        completed = sum(1 for item in self.steps if item.done)
        if self.state == "running" and completed == 0:
            return 0.08
        return min(1.0, completed / len(self.steps))


class JobView(Job):
    """A job with its progress computed, for the wire.

    Progress is a field here and a method on `Job`. A pydantic field that
    shadows a parent property is a trap - the field wins silently and the
    property stops running - so the two are named differently on purpose.
    """

    progress: float = 0.0
    # Inherited from `Job` and never sent: who started a job is this server's
    # business, not the workspace's.
    owner: str = Field(default="", exclude=True)


def _directory():
    return workspaces.root() / JOBS_DIRNAME


def _persist(job: Job) -> None:
    try:
        directory = _directory()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{job.id}.json").write_text(job.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:  # pragma: no cover - a full disk must not kill the work
        logger.warning("Could not mirror job %s to disk: %s", job.id, exc)


def _touch(job: Job) -> Job:
    job.updated_at = _now()
    jobs = _jobs_for()
    with _lock:
        jobs[job.id] = job
    _persist(job)

    # Pushed the moment it changes, to whoever started it. The client still
    # polls - a socket is a way of being early, never the only way of being
    # told - but a step that finishes is on screen in the same second rather
    # than up to a poll later.
    if job.owner:
        hub.publish(workspaces.current(), job.owner, {"job": view(job).model_dump()})

    return job


def create(kind: str, title: str, detail: str, steps: List[str]) -> Job:
    """Register a job before any work starts, so it is visible immediately."""
    job = Job(
        id=uuid4().hex[:ID_LENGTH],
        owner=inbox.current_key(),
        kind=kind,
        title=title.strip() or "Quotation",
        detail=detail.strip(),
        steps=[JobStep(label=label) for label in steps],
        stage="Waiting to start",
    )
    logger.info("Job %s queued: %s (%d steps)", job.id, job.title, len(job.steps))
    return _touch(job)


def start(job_id: str, stage: str = "") -> None:
    job = get(job_id)
    if job is None:
        return
    job.state = "running"
    job.stage = stage or (job.steps[0].label if job.steps else "Working")
    _touch(job)


def step(job_id: str, index: int, stage: str = "") -> None:
    """Mark one step finished. Progress only ever moves on a real completion."""
    job = get(job_id)
    if job is None:
        return
    if 0 <= index < len(job.steps):
        job.steps[index].done = True
    if stage:
        job.stage = stage
    _touch(job)


def stage(job_id: str, text: str) -> None:
    job = get(job_id)
    if job is None:
        return
    job.stage = text
    _touch(job)


def finish(job_id: str, result_ids: List[str]) -> None:
    job = get(job_id)
    if job is None:
        return
    job.state = "done"
    job.stage = "Ready"
    job.result_ids = list(result_ids)
    job.finished_at = _now()
    for item in job.steps:
        item.done = True
    logger.info("Job %s done: %s", job.id, ", ".join(result_ids) or "no result")
    _touch(job)


def fail(job_id: str, message: str) -> None:
    job = get(job_id)
    if job is None:
        return
    job.state = "failed"
    job.stage = "Stopped"
    job.error = message
    job.finished_at = _now()
    logger.error("Job %s failed: %s", job.id, message)
    _touch(job)


def get(job_id: str) -> "Job | None":
    jobs = _jobs_for()
    with _lock:
        job = jobs.get((job_id or "").strip())
    if job is not None:
        return job

    path = _directory() / f"{(job_id or '').strip()}.json"
    if not path.is_file():
        return None
    try:
        recovered = Job.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    with _lock:
        jobs.setdefault(recovered.id, recovered)
    return recovered


def view(job: Job) -> JobView:
    # `owner` stays on disk. Everyone in a workspace can see every job, which is
    # a different question from whose mail it is.
    return JobView(
        **job.model_dump(exclude={"owner"}), progress=round(job.compute_progress(), 4)
    )


def listing(limit: int = 50) -> List[JobView]:
    """Everything on file, newest first."""
    directory = _directory()
    if directory.is_dir():
        for path in directory.glob("*.json"):
            get(path.stem)

    with _lock:
        jobs = list(_jobs_for().values())
    jobs.sort(key=lambda item: (item.created_at, item.id), reverse=True)
    return [view(job) for job in jobs[: max(1, min(limit, 200))]]


def restore() -> None:
    """Bury anything that died mid-flight, in every workspace.

    Every workspace, because a process that stopped stopped for all of them, and
    a job left marked running in a book nobody happened to open first would show
    a bar that never moves again.
    """
    for workspace in workspaces.listing():
        workspaces.use(workspace.id)
        _restore_one()


def _tell_the_owners(dead: List["Job"]) -> None:
    """One note per person whose work did not survive, not one per job.

    A restart kills everything at once; three notes saying the same thing is
    three times the noise for the same fact.
    """
    if not dead:
        return

    from app import inbox  # local: inbox imports jobs' workspace, not jobs

    by_owner: Dict[str, List[Job]] = {}
    for job in dead:
        by_owner.setdefault(job.owner or "local", []).append(job)

    # Addressed by key rather than through the roster: the owner may have left
    # the team since, and they are still the person this happened to.
    for owner, theirs in by_owner.items():
        count = len(theirs)
        inbox.deliver(
            owner,
            "work_lost_to_restart",
            {
                "title": (
                    "Work was lost when the API restarted"
                    if count == 1
                    else f"{count} pieces of work were lost when the API restarted"
                ),
                "body": (
                    f"{theirs[0].title}. Nothing was saved - prepare it again."
                    if count == 1
                    else "Nothing was saved. They need preparing again."
                ),
                "href": "#/jobs",
            },
        )


def _restore_one() -> None:
    """Load this workspace's jobs from disk and fail the ones left running.

    A job marked running in a file is a job whose process is gone. Leaving it
    running would show a bar that never moves again, which is worse than saying
    plainly that it did not finish.
    """
    directory = _directory()
    if not directory.is_dir():
        return

    buried = 0
    buried_jobs: List[Job] = []
    for path in directory.glob("*.json"):
        job = get(path.stem)
        if job is None:
            continue
        if job.state in {"queued", "running"}:
            buried_jobs.append(job)
            job.state = "failed"
            job.stage = "Stopped"
            job.error = (
                "The API restarted while this was running. Nothing was saved - "
                "prepare it again."
            )
            job.finished_at = _now()
            _touch(job)
            buried += 1

    if buried:
        logger.warning("Marked %d job(s) failed: they did not survive a restart", buried)
        _tell_the_owners(buried_jobs)
