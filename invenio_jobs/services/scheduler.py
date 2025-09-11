# -*- coding: utf-8 -*-
#
# Copyright (C) 2024 CERN.
# Copyright (C) 2025 Graz University of Technology.
#
# Invenio-Jobs is free software; you can redistribute it and/or modify it
# under the terms of the MIT License; see LICENSE file for more details.

"""Custom Celery RunScheduler."""

import json
import traceback
import uuid

from celery.beat import ScheduleEntry, Scheduler, logger
from invenio_access.permissions import system_user_id
from invenio_db import db

from invenio_jobs.models import Job, Run
from invenio_jobs.tasks import execute_run
from invenio_jobs.utils import job_arg_json_dumper


class JobEntry(ScheduleEntry):
    """Entry for celery beat."""

    job = None

    def __init__(self, job, *args, **kwargs):
        """Initialise entry."""
        self.job = job
        super().__init__(*args, **kwargs)

    @classmethod
    def from_job(cls, job):
        """Create JobEntry from job."""
        args = json.dumps(job.run_args or job.default_args, default=job_arg_json_dumper)
        args = json.loads(args)
        return cls(
            job=job,
            name=job.title,
            schedule=job.parsed_schedule,
            kwargs={"kwargs": args},
            task=execute_run.name,
            options={"queue": job.default_queue},
            last_run_at=(job.last_run and job.last_run.created),
        )


class RunScheduler(Scheduler):
    """Custom beat scheduler for runs."""

    Entry = JobEntry
    entries = {}

    @property
    def schedule(self):
        """Get currently scheduled entries."""
        return self.entries

    #
    # Celery overrides
    #
    def setup_schedule(self):
        """Setup schedule."""
        self.sync()

    def reserve(self, entry):
        """Update entry to next run execution time."""
        new_entry = self.schedule[entry.job.id] = next(entry)
        return new_entry

    def apply_entry(self, entry, producer=None):
        """Create and apply a JobEntry."""
        with self.app.flask_app.app_context():
            logger.info("Scheduler: Sending due task %s (%s)", entry.name, entry.task)
            try:
                # TODO Only create and send task if there is no "stale" run (status running, starttime > hour, Run pending for > 1 hr)
                run = self.create_run(entry)
                entry.options["task_id"] = str(run.task_id)
                entry.args = (str(run.id), system_user_id)
                result = self.apply_async(entry, producer=producer, advance=False)
            except Exception as exc:
                logger.error(
                    "Message Error: %s\n%s",
                    exc,
                    traceback.format_stack(),
                    exc_info=True,
                )
            else:
                if result and hasattr(result, "id"):
                    logger.debug("%s sent. id->%s", entry.task, result.id)
                else:
                    logger.debug("%s sent.", entry.task)

    def sync(self):
        """Sync Jobs from db to the scheduler."""
        # TODO Should we also have a cleaup task for runs? "stale" run (status running, starttime > hour, Run pending for > 1 hr)
        with self.app.flask_app.app_context():
            jobs = Job.query.filter(
                Job.active.is_(True),
                Job.schedule.isnot(None),
            )
            self.entries = {}  # because some jobs might be deactivated
            for job in jobs:
                self.entries[job.id] = JobEntry.from_job(job)

    #
    # Helpers
    #
    def create_run(self, entry):
        """Create run from a JobEntry."""
        job = db.session.get(Job, entry.job.id)
        # at this point, job arguments should be set, so we send them from here
        # to avoid recomputing them
        run = Run.create(job=job, task_id=uuid.uuid4(), args=entry.kwargs.get("kwargs"))
        db.session.add(run)
        db.session.commit()
        return run
