# -*- coding: utf-8 -*-
#
# Copyright (C) 2024 CERN.
# Copyright (C) 2025 Graz University of Technology.
#
# Invenio-Jobs is free software; you can redistribute it and/or modify it
# under the terms of the MIT License; see LICENSE file for more details.

"""Tasks."""

import traceback
from datetime import datetime, timezone

import sqlalchemy as sa
from celery import shared_task
from flask import current_app, g
from invenio_db import db

from invenio_jobs.errors import TaskExecutionError, TaskExecutionPartialError
from invenio_jobs.logging.jobs import set_job_context
from invenio_jobs.models import Run, RunStatusEnum
from invenio_jobs.proxies import current_jobs


# TODO 1. Move to service? 2. Don't use kwargs?
def update_run(run, **kwargs):
    """Method to update and commit run updates."""
    if not run:
        return
    has_active_subtasks = (
        run.subtasks.filter(
            Run.status.in_([RunStatusEnum.RUNNING.value, RunStatusEnum.QUEUED.value])
        ).count()
        > 0
    )
    current_app.logger.info(
        f"Updating run {run.id} with status {run.status} and active subtasks: {has_active_subtasks}"
    )
    if has_active_subtasks:
        # If there are active subtasks, we keep the run status as RUNNING and simply update the errored entries, if present.
        if errored_entries := kwargs.get("errored_entries", None):
            run.errored_entries += errored_entries
            db.session.commit()
        return
    for kw, value in kwargs.items():
        setattr(run, kw, value)
    db.session.commit()


@shared_task(bind=True, ignore_result=True)
def execute_run(self, run_id, identity_id, kwargs=None):
    """Execute and manage a run state and task."""
    run = Run.query.filter_by(id=run_id).one_or_none()
    task = current_jobs.registry.get(run.job.task).task

    with set_job_context(
        {
            "run_id": str(run_id),
            "job_id": str(run.job.id),
            "identity_id": str(identity_id),
        }
    ):
        update_run(
            run, status=RunStatusEnum.RUNNING, started_at=datetime.now(timezone.utc)
        )
        try:
            current_app.logger.debug(
                f"Executing run {run.id} with task {task.name} and args {kwargs}"
            )
            result = task.apply(kwargs=run.args, throw=True)
            current_app.logger.debug(
                f"Run {run.id} executed successfully with result: {result}"
            )
        except SystemExit as e:
            current_app.logger.error(
                f"Run {run.id} was cancelled by a SystemExit exception: {e}"
            )
            sentry_event_id = getattr(g, "sentry_event_id", None)
            message = (
                f"{e.message} Sentry Event ID: {sentry_event_id}"
                if sentry_event_id
                else e.message
            )
            update_run(
                run,
                status=RunStatusEnum.CANCELLED,
                finished_at=datetime.now(timezone.utc),
                message=message,
            )
            raise e
        except (TaskExecutionPartialError, TaskExecutionError) as e:
            sentry_event_id = getattr(g, "sentry_event_id", None)
            log_message = f"Run {run.id} encountered an error: {e.message}"
            if sentry_event_id:
                log_message += f" With Sentry Event ID: {sentry_event_id}"

            current_app.logger.error(log_message)
            errored_entries_count = getattr(e, "errored_entries_count", 0)
            message = (
                f"{e.message} Sentry Event ID: {sentry_event_id}"
                if sentry_event_id
                else e.message
            )
            update_run(
                run,
                status=RunStatusEnum.PARTIAL_SUCCESS,
                finished_at=datetime.now(timezone.utc),
                message=message,
                errored_entries=errored_entries_count,
            )
            return
        except Exception as e:
            sentry_event_id = getattr(g, "sentry_event_id", None)
            log_message = f"Run {run.id} encountered an unexpected error: {e}"
            if sentry_event_id:
                log_message += f" With Sentry Event ID: {sentry_event_id}"

            current_app.logger.error(log_message)
            message = f"{e.__class__.__name__}: {str(e)}\n{traceback.format_exc()}"
            if sentry_event_id:
                message += f" Sentry Event ID: {sentry_event_id}"
            update_run(
                run,
                status=RunStatusEnum.FAILED,
                finished_at=datetime.now(timezone.utc),
                message=message,
            )
            return
        finally:
            db.session.execute(
                sa.update(Run).where(Run.id == run.id).values(subtasks_closed=True)
            )
            db.session.commit()
        update_run(
            run,
            status=RunStatusEnum.SUCCESS,
            finished_at=datetime.now(timezone.utc),
        )
