# -*- coding: utf-8 -*-
#
# Copyright (C) 2024 CERN.
#
# Invenio-Jobs is free software; you can redistribute it and/or modify it
# under the terms of the MIT License; see LICENSE file for more details.

"""Tasks."""
from datetime import datetime, timezone

from celery import shared_task
from invenio_db import db

from invenio_jobs.models import Run, RunStatusEnum


# TODO 1. Move to service? 2. Don't use kwargs?
def update_run(run, **kwargs):
    """Method to update and commit run updates."""
    if not run:
        return
    for kw, value in kwargs.items():
        setattr(run, kw, value)
    db.session.commit()


@shared_task(bind=True, ignore_result=True)
def execute_run(self, run_id, kwargs=None):
    """Execute and manage a run state and task."""
    run = Run.query.filter_by(id=run_id).one_or_none()
    task = self.app.tasks.get(run.job.task)
    update_run(run, status=RunStatusEnum.RUNNING, started_at=datetime.now(timezone.utc))

    try:
        result = task.apply(kwargs=kwargs, throw=True)
    except SystemExit as e:
        update_run(
            run,
            status=RunStatusEnum.CANCELLED,
            finished_at=datetime.now(timezone.utc),
        )
        raise e
    except Exception as e:
        update_run(
            run,
            status=RunStatusEnum.FAILED,
            finished_at=datetime.now(timezone.utc),
        )
        return

    update_run(
        run,
        status=RunStatusEnum.SUCCESS,
        finished_at=datetime.now(timezone.utc),
    )
