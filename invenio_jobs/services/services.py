# -*- coding: utf-8 -*-
#
# Copyright (C) 2024 CERN.
# Copyright (C) 2024 University of Münster.
# Copyright (C) 2025 Graz University of Technology.
# Copyright (C) 2025 KTH Royal Institute of Technology.
#
# Invenio-Jobs is free software; you can redistribute it and/or modify it
# under the terms of the MIT License; see LICENSE file for more details.

"""Service definitions."""

import json
import uuid
from datetime import datetime

import sqlalchemy as sa
from flask import current_app
from invenio_access.permissions import system_user_id
from invenio_db import db
from invenio_records_resources.services.base import LinksTemplate
from invenio_records_resources.services.base.utils import map_search_params
from invenio_records_resources.services.records import RecordService
from invenio_records_resources.services.uow import (
    ModelCommitOp,
    ModelDeleteOp,
    TaskOp,
    TaskRevokeOp,
    unit_of_work,
)

from invenio_jobs.logging.jobs import EMPTY_JOB_CTX, with_job_context
from invenio_jobs.tasks import execute_run

from ..api import AttrDict
from ..models import Job, Run, RunStatusEnum, Task
from .errors import (
    JobNotFoundError,
    RunNotFoundError,
    RunStatusChangeError,
)


class BaseService(RecordService):
    """Base service class for DB-backed services.

    NOTE: See https://github.com/inveniosoftware/invenio-records-resources/issues/583
    for future directions.
    """

    def rebuild_index(self, identity, uow=None):
        """Raise error since services are not backed by search indices."""
        raise NotImplementedError()


class TasksService(BaseService):
    """Tasks service."""

    def read_registered_task_arguments(self, identity, registered_task_id):
        """Return arguments allowed for given task."""
        self.require_permission(identity, "read")

        task = Task.get(registered_task_id)
        if task.arguments_schema:
            return task.arguments_schema()


def get_job(job_id):
    """Get a job by id."""
    job = db.session.get(Job, job_id)
    if job is None:
        raise JobNotFoundError(job_id)
    return job


def get_run(run_id=None, job_id=None):
    """Get a job by id."""
    run = db.session.get(Run, run_id)
    if isinstance(job_id, str):
        job_id = uuid.UUID(job_id)

    if run is None or run.job_id != job_id:
        raise RunNotFoundError(run_id, job_id=job_id)
    return run


class JobsService(BaseService):
    """Jobs service."""

    def get(self, identity, id_, params=None):
        """Get a job by id."""
        self.require_permission(identity, "read")
        job = get_job(id_)
        return self.result_item(self, identity, job, links_tpl=self.links_item_tpl)

    @unit_of_work()
    def create(self, identity, data, uow=None):
        """Create a job."""
        self.require_permission(identity, "create")

        # TODO: See if we need extra validation (e.g. tasks, args, etc.)
        valid_data, errors = self.schema.load(
            data,
            context={"identity": identity},
            raise_errors=True,
        )

        job = Job(**valid_data)
        uow.register(ModelCommitOp(job))
        return self.result_item(self, identity, job, links_tpl=self.links_item_tpl)

    def search(self, identity, params):
        """Search for jobs."""
        self.require_permission(identity, "search")

        filters = []
        search_params = map_search_params(self.config.search, params)

        query_param = search_params["q"]
        if query_param:
            filters.append(
                sa.or_(
                    Job.title.ilike(f"%{query_param}%"),
                    Job.description.ilike(f"%{query_param}%"),
                )
            )

        jobs = (
            Job.query.filter(*filters)
            .order_by(
                search_params["sort_direction"](
                    sa.text(",".join(search_params["sort"]))
                )
            )
            .paginate(
                page=search_params["page"],
                per_page=search_params["size"],
                error_out=False,
            )
        )

        return self.result_list(
            self,
            identity,
            jobs,
            params=search_params,
            links_tpl=LinksTemplate(self.config.links_search, context={"args": params}),
            links_item_tpl=self.links_item_tpl,
        )

    def read(self, identity, id_):
        """Retrieve a job."""
        self.require_permission(identity, "read")
        job = get_job(id_)
        return self.result_item(self, identity, job, links_tpl=self.links_item_tpl)

    @unit_of_work()
    def update(self, identity, id_, data, uow=None):
        """Update a job."""
        self.require_permission(identity, "update")

        job = get_job(id_)

        valid_data, errors = self.schema.load(
            data,
            context={"identity": identity, "job": job},
            raise_errors=True,
        )

        for key, value in valid_data.items():
            if key == "run_args":
                job.set_run_args(value)
            else:
                setattr(job, key, value)
        uow.register(ModelCommitOp(job))
        return self.result_item(self, identity, job, links_tpl=self.links_item_tpl)

    @unit_of_work()
    def delete(self, identity, id_, uow=None):
        """Delete a job."""
        self.require_permission(identity, "delete")
        job = get_job(id_)

        # TODO: Check if we can delete the job (e.g. if there are still active Runs).
        # That also depends on the FK constraints in the DB.
        uow.register(ModelDeleteOp(job))

        return True


class RunsService(BaseService):
    """Runs service."""

    def get(self, identity, job_id, run_id):
        """Get a run by id."""
        self.require_permission(identity, "read")
        run = get_run(job_id=job_id, run_id=run_id)
        return self.result_item(self, identity, run, links_tpl=self.links_item_tpl)

    def search(self, identity, job_id, params):
        """Search for runs."""
        self.require_permission(identity, "search")

        filters = [
            Run.job_id == job_id,
        ]
        include_subtasks = params.get("include_subtasks") in ("true", "1", True)

        if not include_subtasks:
            filters.append(Run.parent_run_id == None)

        search_params = map_search_params(self.config.search, params)

        query_param = search_params["q"]
        if query_param:
            filters.append(
                sa.or_(
                    Run.id.ilike(f"%{query_param}%"),
                    Run.task_id.ilike(f"%{query_param}%"),
                )
            )

        runs = (
            Run.query.filter(*filters)
            .order_by(
                search_params["sort_direction"](
                    sa.text(",".join(search_params["sort"]))
                )
            )
            .paginate(
                page=search_params["page"],
                per_page=search_params["size"],
                error_out=False,
            )
        )

        return self.result_list(
            self,
            identity,
            runs,
            params=search_params,
            links_tpl=LinksTemplate(self.config.links_search, context={"args": params}),
            links_item_tpl=self.links_item_tpl,
        )

    def read(self, identity, job_id, run_id):
        """Retrieve a run."""
        self.require_permission(identity, "read")
        run = get_run(job_id=job_id, run_id=run_id)
        run_dict = run.dump()
        run_record = AttrDict(run_dict)
        return self.result_item(
            self, identity, run_record, links_tpl=self.links_item_tpl
        )

    @with_job_context(EMPTY_JOB_CTX)
    @unit_of_work()
    def create(self, identity, job_id, data, uow=None):
        """Create a run."""
        self.require_permission(identity, "create")
        job = get_job(job_id)

        # TODO: See if we need extra validation (e.g. tasks, args, etc.)
        valid_data, errors = self.schema.load(
            data,
            context={"identity": identity, "job": job},
            raise_errors=True,
        )

        run = Run.create(
            job=job,
            id=str(uuid.uuid4()),
            task_id=str(uuid.uuid4()),
            started_by_id=(
                None if identity.id == system_user_id else identity.id
            ),  # None because column expects Integer FK but is nullable
            status=RunStatusEnum.QUEUED,
            **valid_data,
        )

        uow.register(ModelCommitOp(run))
        uow.register(
            TaskOp.for_async_apply(
                execute_run,
                kwargs={"run_id": run.id, "identity_id": identity.id},
                task_id=str(run.task_id),
                queue=run.queue,
            )
        )
        current_app.logger.debug("Run created")

        return self.result_item(self, identity, run, links_tpl=self.links_item_tpl)

    @unit_of_work()
    def add_total_entries(self, identity, run_id, job_id, total_entries, uow=None):
        """Increment the total entries of a run atomically."""
        self.require_permission(identity, "update")

        if not isinstance(total_entries, int):
            raise ValueError("total_entries must be an integer")
        if total_entries < 0:
            raise ValueError("total_entries cannot be negative")

        # Atomic UPDATE ... SET total_entries = total_entries + :inc
        stmt = (
            sa.update(Run)
            .where(Run.id == run_id, Run.job_id == job_id)
            .values(total_entries=Run.total_entries + sa.bindparam("inc"))
            .returning(Run.id, Run.total_entries)  # lets us avoid refresh()
        )
        res = db.session.execute(stmt, {"inc": total_entries})
        row = res.first()
        if not row:
            raise RunNotFoundError(run_id, job_id=job_id)

        # Reload ORM instance
        run = db.session.get(Run, row.id)

        uow.register(ModelCommitOp(run))
        return self.result_item(self, identity, run, links_tpl=self.links_item_tpl)

    @unit_of_work()
    def create_subtask_run(self, identity, parent_run_id, job_id, args=None, uow=None):
        """Create a new subtask Run."""
        self.require_permission(identity, "create")
        parent_run = get_run(run_id=parent_run_id, job_id=job_id)
        job = parent_run.job

        subtask_run = Run.create(
            job=job,
            id=str(uuid.uuid4()),
            task_id=str(uuid.uuid4()),
            started_by_id=(
                None if identity.id == system_user_id else identity.id
            ),  # None because column expects Integer FK but is nullable
            status=RunStatusEnum.QUEUED,
            title=f"Run {parent_run_id} — Subtask",
            args=args or {},
        )

        parent_run.total_subtasks += 1
        subtask_run.parent_run_id = parent_run.id
        uow.register(ModelCommitOp(subtask_run))
        uow.register(ModelCommitOp(parent_run))
        return self.result_item(
            self, identity, subtask_run, links_tpl=self.links_item_tpl
        )

    @unit_of_work()
    def start_processing_subtask(self, identity, run_id, job_id, uow=None):
        """Start processing a subtask."""
        self.require_permission(identity, "update")
        run = get_run(run_id=run_id, job_id=job_id)

        if run.status != RunStatusEnum.QUEUED:
            raise RunStatusChangeError(run, RunStatusEnum.RUNNING)

        run.status = RunStatusEnum.RUNNING
        run.started_at = datetime.utcnow()

        uow.register(ModelCommitOp(run))
        return self.result_item(self, identity, run, links_tpl=self.links_item_tpl)

    @unit_of_work()
    def finalize_subtask(
        self,
        identity,
        run_id,
        job_id,
        errored_entries_count=0,
        success=True,
        inserted_entries_count=0,
        updated_entries_count=0,
        uow=None,
    ):
        """Finalize a subtask and update its parent."""
        self.require_permission(identity, "update")

        # Child run: load and set its status
        run = get_run(run_id=run_id, job_id=job_id)
        run.status = RunStatusEnum.SUCCESS if success else RunStatusEnum.FAILED

        # Compute increments
        fail_inc = 0 if success else 1
        err_inc = int(errored_entries_count or 0)
        ins_inc = int(inserted_entries_count or 0)
        upd_inc = int(updated_entries_count or 0)

        # Atomically increment parent counters and fetch the new values
        parent_counters_stmt = (
            sa.update(Run)
            .where(Run.id == run.parent_run_id)
            .values(
                errored_entries=Run.errored_entries + sa.bindparam("err_inc"),
                completed_subtasks=Run.completed_subtasks + 1,
                failed_subtasks=Run.failed_subtasks + sa.bindparam("fail_inc"),
                inserted_entries=Run.inserted_entries + sa.bindparam("ins_inc"),
                updated_entries=Run.updated_entries + sa.bindparam("upd_inc"),
            )
            .returning(
                Run.id,
                Run.completed_subtasks,
                Run.total_subtasks,
                Run.failed_subtasks,
                Run.errored_entries,
                Run.total_entries,
                Run.subtasks_closed,
                Run.inserted_entries,
                Run.updated_entries,
            )
        )
        res = db.session.execute(
            parent_counters_stmt,
            {
                "err_inc": err_inc,
                "fail_inc": fail_inc,
                "ins_inc": ins_inc,
                "upd_inc": upd_inc,
            },
        )
        row = res.first()
        if not row:
            raise RunNotFoundError(run.parent_run_id, job_id=job_id)

        (
            parent_id,
            completed,
            total,
            failed,
            parent_errored,
            total_entries,
            subtasks_closed,
            parent_inserted,
            parent_updated,
        ) = row

        parts = [f"{completed}/{total} subtasks completed."]
        if failed:
            parts.append(f" {failed} subtasks with errors.")
        if parent_errored > 0:
            parts.append(f" {parent_errored}/{total_entries} entries errored.")
        if parent_inserted or parent_updated:
            parts.append(f" {parent_inserted} inserted / {parent_updated} updated.")
        progress_msg = "".join(parts)
        # Only update parent status/finished_at if all subtasks are completed and them main job is not running.
        finished = completed == total
        if subtasks_closed and finished:
            if failed == 0 and parent_errored == 0:
                parent_status = RunStatusEnum.SUCCESS
            elif failed < total or (
                parent_errored > 0 and parent_errored < total_entries
            ):
                parent_status = RunStatusEnum.PARTIAL_SUCCESS
            else:
                parent_status = RunStatusEnum.FAILED
            finished_at_value = datetime.utcnow()
        else:
            parent_status = RunStatusEnum.RUNNING
            finished_at_value = None

        update_parent_stmt = (
            sa.update(Run)
            .where(Run.id == parent_id)
            .values(
                message=progress_msg,
                status=parent_status,
                finished_at=finished_at_value,
            )
        )
        db.session.execute(update_parent_stmt)

        uow.register(ModelCommitOp(run))

        return self.result_item(self, identity, run, links_tpl=self.links_item_tpl)

    @unit_of_work()
    def update(self, identity, job_id, run_id, data, uow=None):
        """Update a run."""
        self.require_permission(identity, "update")

        run = get_run(job_id=job_id, run_id=run_id)

        valid_data, errors = self.schema.load(
            data,
            context={"identity": identity, "run": run, "job": run.job},
            raise_errors=True,
        )

        for key, value in valid_data.items():
            setattr(run, key, value)

        uow.register(ModelCommitOp(run))
        return self.result_item(self, identity, run, links_tpl=self.links_item_tpl)

    @unit_of_work()
    def delete(self, identity, job_id, run_id, uow=None):
        """Delete a run."""
        self.require_permission(identity, "delete")
        run = get_run(job_id=job_id, run_id=run_id)

        # TODO: Check if we can delete the run (e.g. if it's still running).
        uow.register(ModelDeleteOp(run))

        return True

    @unit_of_work()
    def stop(self, identity, job_id, run_id, uow=None):
        """Stop a run."""
        self.require_permission(identity, "stop")
        run = get_run(job_id=job_id, run_id=run_id)

        if run.status not in (RunStatusEnum.QUEUED, RunStatusEnum.RUNNING):
            raise RunStatusChangeError(run, RunStatusEnum.CANCELLING)

        run.status = RunStatusEnum.CANCELLING
        uow.register(ModelCommitOp(run))
        uow.register(TaskRevokeOp(str(run.task_id)))

        return self.result_item(self, identity, run, links_tpl=self.links_item_tpl)


class JobLogService(BaseService):
    """Job log service."""

    def search(self, identity, params):
        """Search for app logs."""
        self.require_permission(identity, "search")
        search_after = params.pop("search_after", None)
        search = self._search(
            "search",
            identity,
            params,
            None,
            permission_action="read",
        )
        max_docs = current_app.config["JOBS_LOGS_MAX_RESULTS"]
        batch_size = current_app.config["JOBS_LOGS_BATCH_SIZE"]

        # Clone and strip version before counting
        count_search = search._clone()
        count_search._params.pop("version", None)  # strip unsupported param
        total = count_search.count()

        # Track if we're truncating results
        truncated = total > max_docs

        search = search.sort("@timestamp", "_id").extra(size=batch_size)
        if search_after:
            search = search.extra(search_after=search_after)

        final_results = None
        fetched_count = 0

        # Keep fetching until we have max_docs or no more results
        while fetched_count < max_docs:
            results = search.execute()
            hits = results.hits
            if not hits:
                if final_results is None:
                    final_results = results
                break

            if not final_results:
                final_results = results  # keep metadata from first page
            else:
                final_results.hits.extend(hits)
                final_results.hits.hits.extend(hits.hits)

            fetched_count += len(hits)

            # Stop if we've reached the limit
            if fetched_count >= max_docs:
                # Trim to exact max_docs
                final_results.hits.hits = final_results.hits.hits[:max_docs]
                final_results.hits[:] = final_results.hits[:max_docs]
                break

            search = search.extra(search_after=hits[-1].meta.sort)

        # Store truncation info in the result for the AppLogsList to use
        if final_results and truncated:
            final_results._truncated = True
            final_results._total_available = total
            final_results._max_docs = max_docs

        return self.result_list(
            self,
            identity,
            final_results,
            links_tpl=self.links_item_tpl,
        )
