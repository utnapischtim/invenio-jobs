# -*- coding: utf-8 -*-
#
# Copyright (C) 2024 CERN.
# Copyright (C) 2025 Graz University of Technology.
#
# Invenio-Jobs is free software; you can redistribute it and/or modify it
# under the terms of the MIT License; see LICENSE file for more details.

"""Resource tests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from time import sleep
from unittest.mock import patch

import pytest

from invenio_jobs.logging.jobs import EMPTY_JOB_CTX, job_context
from invenio_jobs.tasks import execute_run


@patch.object(execute_run, "apply_async")
def test_simple_flow(mock_apply_async, app, db, client, user):
    """Test simple flow."""
    client = user.login(client)
    job_payload = {
        "title": "Test job",
        "task": "update_expired_embargos",
        "description": "Test description",
        "active": False,
        "default_queue": "low",
        "schedule": {"type": "interval", "hours": 4},
    }

    # Create a job
    res = client.post("/jobs", json=job_payload)
    assert res.status_code == 201
    job_id = res.json["id"]
    expected_job = {
        "id": job_id,
        "title": "Test job",
        "description": "Test description",
        "active": False,
        "task": "update_expired_embargos",
        "default_queue": "low",
        "default_args": '{"since": null}',
        "run_args": None,
        "schedule": {"type": "interval", "hours": 4},
        "created": res.json["created"],
        "updated": res.json["updated"],
        "links": {
            "self": f"https://127.0.0.1:5000/api/jobs/{job_id}",
            "runs": f"https://127.0.0.1:5000/api/jobs/{job_id}/runs",
            "self_admin_html": f"https://127.0.0.1:5000/administration/jobs/{job_id}",
        },
    }

    assert res.json == expected_job

    # Activate the job (i.e. update)
    job_payload["args"] = {}
    job_payload["custom_args"] = "{}"

    res = client.put(
        f"/jobs/{job_id}",
        json={**job_payload, "active": True},
    )
    assert res.status_code == 200
    expected_job["active"] = True
    expected_job["updated"] = res.json["updated"]
    expected_job["run_args"] = {}

    assert res.json == expected_job

    list_repr = deepcopy(expected_job)
    list_repr.update(
        {
            "last_run": {
                "title": "Manual run",
                "total_subtasks": 0,
                "completed_subtasks": 0,
                "failed_subtasks": 0,
                "errored_entries": 0,
                "total_entries": 0,
            },
            "last_runs": {
                "cancelled": {},
                "cancelling": {},
                "failed": {},
                "partial_success": {},
                "queued": {},
                "running": {},
                "success": {},
                "warning": {},
            },
        }
    )

    # List jobs
    res = client.get("/jobs")
    assert res.status_code == 200
    assert res.json["hits"]["total"] == 1
    assert res.json["hits"]["hits"][0] == list_repr

    # Get job
    res = client.get(f"/jobs/{job_id}")
    assert res.status_code == 200
    assert res.json == expected_job

    # Create/trigger a run with invalid `since` to test correct failure
    invalid_since_arg = "2025-05-20T00:00:00+aa:bb"  # slightly invalid ISO timestamp
    res = client.post(
        f"/jobs/{job_id}/runs",
        json={
            "title": "Manually triggered run",
            "args": {
                "since": invalid_since_arg,
                "job_arg_schema": "PredefinedArgsSchema",
            },
            "queue": "celery",
        },
    )
    assert res.status_code == 400

    # Create/trigger a run with no inputs, expecting it to rely on a default value for `since`
    res = client.post(
        f"/jobs/{job_id}/runs",
        json={
            "title": "Manually triggered run",
            "args": {},
            "queue": "celery",
        },
    )
    assert res.status_code == 201

    run_id = res.json["id"]
    expected_run = {
        "id": run_id,
        "job_id": job_id,
        "created": res.json["created"],
        "updated": res.json["updated"],
        "started_by_id": int(user.id),
        "started_by": {
            "id": str(user.id),
            "username": user.username,
            "profile": user._user_profile,
            "identities": {},
            "is_current_user": True,
            "type": "user",
            "links": {},
        },
        "started_at": res.json["started_at"],
        "finished_at": res.json["finished_at"],
        "status": "QUEUED",
        "message": None,
        "task_id": "ce6d9e62-aee1-4f52-b9fd-20ec61fbf55a",
        "title": "Manually triggered run",
        # No schema assigned as the required `job_arg_schema` was not specified in the request
        "args": {"job_arg_schema": "custom"},
        "queue": "celery",
        "parent_run_id": None,
        "total_subtasks": 0,
        "completed_subtasks": 0,
        "failed_subtasks": 0,
        "errored_entries": 0,
        "total_entries": 0,
        "subtasks": [],
        "links": {
            "self": f"https://127.0.0.1:5000/api/jobs/{job_id}/runs/{run_id}",
            "logs": f"https://127.0.0.1:5000/api/logs/jobs?q={run_id}",
            "stop": f"https://127.0.0.1:5000/api/jobs/{job_id}/runs/{run_id}/actions/stop",
        },
    }

    # Create/trigger a run with valid inputs
    since_arg = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    res = client.post(
        f"/jobs/{job_id}/runs",
        json={
            "title": "Manually triggered run",
            "args": {"since": since_arg, "job_arg_schema": "PredefinedArgsSchema"},
            "queue": "celery",
        },
    )
    assert res.status_code == 201

    run_id = res.json["id"]
    expected_run = {
        "id": run_id,
        "job_id": job_id,
        "started_by_id": int(user.id),
        "started_by": {
            "id": str(user.id),
            "username": user.username,
            "profile": user._user_profile,
            "identities": {},
            "is_current_user": True,
            "type": "user",
            "links": {},
        },
        "started_at": res.json["started_at"],
        "finished_at": res.json["finished_at"],
        "status": "QUEUED",
        "message": None,
        "title": "Manually triggered run",
        "args": {"job_arg_schema": "custom"},
        "queue": "celery",
        "created": res.json["created"],
        "updated": res.json["updated"],
        "parent_run_id": None,
        "total_subtasks": 0,
        "completed_subtasks": 0,
        "failed_subtasks": 0,
        "errored_entries": 0,
        "total_entries": 0,
        "subtasks": [],
        "links": {
            "self": f"https://127.0.0.1:5000/api/jobs/{job_id}/runs/{run_id}",
            "logs": f"https://127.0.0.1:5000/api/logs/jobs?q={run_id}",
            "stop": f"https://127.0.0.1:5000/api/jobs/{job_id}/runs/{run_id}/actions/stop",
        },
    }
    assert "task_id" in res.json
    assert res.json["task_id"] != None
    expected_run["task_id"] = res.json["task_id"]
    assert res.json == expected_run

    list_expected_run = deepcopy(expected_run)
    list_expected_run.pop("started_by")
    list_expected_run.pop("subtasks")
    list_expected_run["args"] = {
        "args": {"since": since_arg},
        "job_arg_schema": "custom",
    }
    # List runs
    res = client.get(f"/jobs/{job_id}/runs")
    assert res.status_code == 200
    assert (
        res.json["hits"]["total"] == 2
    )  # We expect to have created 2 non-error runs so far
    assert res.json["hits"]["hits"][0] == list_expected_run

    # Get run
    res = client.get(f"/jobs/{job_id}/runs/{run_id}")
    assert res.status_code == 200
    assert res.json == list_expected_run

    # Stop run
    res = client.post(f"/jobs/{job_id}/runs/{run_id}/actions/stop")
    assert res.status_code == 202
    assert res.json["status"] == "CANCELLING"

    # edit the job args
    job_payload["args"] = {
        "test_flag": True,
        "since": "2025-11-20T12:18:00+00:00",
    }
    job_payload[
        "custom_args"
    ] = """
    {
        "custom_arg1": "value1",
        "custom_arg2": {"nested_arg1": "nested_value1"},
        "test_flag": false
    }
    """
    res = client.put(
        f"/jobs/{job_id}",
        json={**job_payload, "active": True},
    )
    assert res.status_code == 200
    expected_job["run_args"] = {
        "test_flag": True,
        "since": "2025-11-20T12:18:00+00:00",
        "custom_args": {
            "custom_arg1": "value1",
            "custom_arg2": {"nested_arg1": "nested_value1"},
            "test_flag": False,
        },
    }
    expected_job["default_args"] = (
        '{"test_flag": false, "custom_arg1": "value1", "custom_arg2": {"nested_arg1": "nested_value1"}}'
    )

    assert res.json["run_args"] == expected_job["run_args"]
    assert res.json["default_args"] == expected_job["default_args"]

    # start a run with the new job args and check if it uses the provided defaults
    res = client.post(
        f"/jobs/{job_id}/runs",
        json={
            "title": "Manually triggered run",
            "queue": "celery",
        },
    )
    assert res.status_code == 201
    expected_run_args = {
        "args": {
            "custom_arg1": "value1",
            "custom_arg2": {"nested_arg1": "nested_value1"},
            "test_flag": False,
        },
        "job_arg_schema": "custom",
    }

    res = client.get(f"/jobs/{job_id}/runs")
    assert res.status_code == 200
    assert res.json["hits"]["total"] == 3
    assert res.json["hits"]["hits"][0]["args"] == expected_run_args

    # Search for log jobs, first set the logger level to INFO
    # and log a message by setting the job context
    job_context.set(dict(job_id=job_id, run_id=run_id, identity_id=str(user.id)))
    app.logger.setLevel("INFO")
    app.logger.info("Test log message")
    sleep(1)  # Wait for log to be indexed
    res = client.get(f"/logs/jobs?q={job_id}")
    assert res.status_code == 200
    assert res.json["hits"]["total"] == 1
    assert res.json["hits"]["hits"][0]["message"] == "Test log message"
    job_context.set(EMPTY_JOB_CTX)


@pytest.mark.skip("Tasks search not needed.")
def test_tasks_search(client):
    """Test tasks search."""
    mock_task_res = {
        "name": "update_expired_embargos",
        "description": "Mock task description.",
        "links": {},
        "parameters": {
            "arg1": {
                "name": "arg1",
                "default": None,
                "kind": "POSITIONAL_OR_KEYWORD",
            },
            "arg2": {
                "name": "arg2",
                "default": None,
                "kind": "POSITIONAL_OR_KEYWORD",
            },
            "kwarg1": {
                "name": "kwarg1",
                "default": None,
                "kind": "POSITIONAL_OR_KEYWORD",
            },
            "kwarg2": {
                "name": "kwarg2",
                "default": False,
                "kind": "POSITIONAL_OR_KEYWORD",
            },
            "kwarg3": {
                "name": "kwarg3",
                "default": "always",
                "kind": "POSITIONAL_OR_KEYWORD",
            },
        },
    }
    res = client.get("/tasks")
    assert res.status_code == 200
    assert "hits" in res.json
    # We can't know exactly what tasks will be in the results
    assert res.json["hits"]["total"] > 0
    assert mock_task_res in res.json["hits"]["hits"]

    # Test filtering
    res = client.get("/tasks?q=mock_task")
    assert res.status_code == 200
    assert res.json["hits"]["total"] == 1
    assert mock_task_res == res.json["hits"]["hits"][0]


def test_jobs_create(db, client):
    """Test job creation."""
    # Test minimal job payload
    res = client.post(
        "/jobs",
        json={
            "title": "Test minimal job",
            "task": "update_expired_embargos",
        },
    )
    assert res.status_code == 201
    assert res.json == {
        "id": res.json["id"],
        "title": "Test minimal job",
        "description": None,
        "active": True,
        "task": "update_expired_embargos",
        "default_queue": "celery",
        "default_args": '{"since": null}',
        "run_args": None,
        "schedule": None,
        "created": res.json["created"],
        "updated": res.json["updated"],
        "links": {
            "runs": f"https://127.0.0.1:5000/api/jobs/{res.json['id']}/runs",
            "self": f"https://127.0.0.1:5000/api/jobs/{res.json['id']}",
            "self_admin_html": f"https://127.0.0.1:5000/administration/jobs/{res.json['id']}",
        },
    }

    # Test full job payload
    res = client.post(
        "/jobs",
        json={
            "title": "Test full job",
            "task": "update_expired_embargos",
            "description": "Test description",
            "active": False,
            "default_queue": "low",
            "default_args": "{}",
            "schedule": {"type": "interval", "hours": 4},
        },
    )
    assert res.status_code == 201
    assert res.json == {
        "id": res.json["id"],
        "title": "Test full job",
        "description": "Test description",
        "active": False,
        "task": "update_expired_embargos",
        "default_queue": "low",
        "default_args": '{"since": null}',
        "run_args": None,
        "schedule": {"type": "interval", "hours": 4},
        "created": res.json["created"],
        "updated": res.json["updated"],
        "links": {
            "runs": f"https://127.0.0.1:5000/api/jobs/{res.json['id']}/runs",
            "self": f"https://127.0.0.1:5000/api/jobs/{res.json['id']}",
            "self_admin_html": f"https://127.0.0.1:5000/administration/jobs/{res.json['id']}",
        },
    }


def test_jobs_update(db, client, jobs):
    """Test job updates."""
    # Update existing job
    res = client.put(
        f"/jobs/{jobs.simple.id}",
        json={
            "title": "Test updated job",
            "task": "update_expired_embargos",
            "description": "Test updated description",
            "schedule": {"type": "interval", "hours": 2},
            "active": False,
            "default_queue": "celery",
            "default_args": "{}",
            "args": {},
            "custom_args": "{}",
        },
    )
    assert res.status_code == 200
    updated_job = {
        "id": jobs.simple.id,
        "title": "Test updated job",
        "description": "Test updated description",
        "active": False,
        "task": "update_expired_embargos",
        "default_queue": "celery",
        "default_args": '{"since": null}',
        "run_args": {},
        "schedule": {"type": "interval", "hours": 2},
        "created": jobs.simple["created"],
        "updated": res.json["updated"],
        "links": {
            "runs": f"https://127.0.0.1:5000/api/jobs/{jobs.simple.id}/runs",
            "self": f"https://127.0.0.1:5000/api/jobs/{jobs.simple.id}",
            "self_admin_html": f"https://127.0.0.1:5000/administration/jobs/{res.json['id']}",
        },
    }
    assert res.json == updated_job

    # Read the job to check the update
    res = client.get(f"/jobs/{jobs.simple.id}")
    assert res.status_code == 200
    assert res.json == updated_job


def test_jobs_search(client, jobs):
    """Test jobs search."""
    res = client.get("/jobs")
    assert res.status_code == 200
    assert "hits" in res.json
    assert res.json["hits"]["total"] == 3
    hits = res.json["hits"]["hits"]

    interval_job_res = next((j for j in hits if j["id"] == jobs.interval.id), None)
    assert interval_job_res == {
        "id": jobs.interval.id,
        "title": "Test interval job",
        "description": None,
        "active": True,
        "task": "update_expired_embargos",
        "default_queue": "low",
        "default_args": '{"since": null}',
        "run_args": None,
        "schedule": {
            "type": "interval",
            "hours": 4,
        },
        "last_run": {
            "title": "Manual run",
            "total_subtasks": 0,
            "completed_subtasks": 0,
            "failed_subtasks": 0,
            "errored_entries": 0,
            "total_entries": 0,
        },
        "last_runs": {
            "cancelled": {},
            "cancelling": {},
            "failed": {},
            "partial_success": {},
            "queued": {},
            "running": {},
            "success": {},
            "warning": {},
        },
        "created": jobs.interval["created"],
        "updated": jobs.interval["updated"],
        "links": {
            "runs": f"https://127.0.0.1:5000/api/jobs/{jobs.interval.id}/runs",
            "self": f"https://127.0.0.1:5000/api/jobs/{jobs.interval.id}",
            "self_admin_html": f"https://127.0.0.1:5000/administration/jobs/{jobs.interval.id}",
        },
    }

    crontab_job_res = next((j for j in hits if j["id"] == jobs.crontab.id), None)
    assert crontab_job_res == {
        "id": jobs.crontab.id,
        "title": "Test crontab job",
        "description": None,
        "active": True,
        "task": "update_expired_embargos",
        "default_queue": "low",
        "default_args": '{"since": null}',
        "run_args": None,
        "schedule": {
            "type": "crontab",
            "minute": "0",
            "hour": "0",
            "day_of_week": "*",
            "day_of_month": "*",
            "month_of_year": "*",
        },
        "last_run": {
            "title": "Manual run",
            "total_subtasks": 0,
            "completed_subtasks": 0,
            "failed_subtasks": 0,
            "errored_entries": 0,
            "total_entries": 0,
        },
        "last_runs": {
            "cancelled": {},
            "cancelling": {},
            "failed": {},
            "partial_success": {},
            "queued": {},
            "running": {},
            "success": {},
            "warning": {},
        },
        "created": jobs.crontab["created"],
        "updated": jobs.crontab["updated"],
        "links": {
            "runs": f"https://127.0.0.1:5000/api/jobs/{jobs.crontab.id}/runs",
            "self": f"https://127.0.0.1:5000/api/jobs/{jobs.crontab.id}",
            "self_admin_html": f"https://127.0.0.1:5000/administration/jobs/{jobs.crontab.id}",
        },
    }

    simple_job_res = next((j for j in hits if j["id"] == jobs.simple.id), None)
    assert simple_job_res == {
        "id": jobs.simple.id,
        "title": "Test unscheduled job",
        "description": None,
        "active": True,
        "task": "update_expired_embargos",
        "default_queue": "low",
        "default_args": '{"since": null}',
        "run_args": None,
        "schedule": None,
        "last_run": {
            "title": "Manual run",
            "total_subtasks": 0,
            "completed_subtasks": 0,
            "failed_subtasks": 0,
            "errored_entries": 0,
            "total_entries": 0,
        },
        "last_runs": {
            "cancelled": {},
            "cancelling": {},
            "failed": {},
            "partial_success": {},
            "queued": {},
            "running": {},
            "success": {},
            "warning": {},
        },
        "created": jobs.simple["created"],
        "updated": jobs.simple["updated"],
        "links": {
            "runs": f"https://127.0.0.1:5000/api/jobs/{jobs.simple.id}/runs",
            "self": f"https://127.0.0.1:5000/api/jobs/{jobs.simple.id}",
            "self_admin_html": f"https://127.0.0.1:5000/administration/jobs/{jobs.simple.id}",
        },
    }

    # Test filtering
    res = client.get("/jobs?q=interval")
    assert res.status_code == 200
    assert res.json["hits"]["total"] == 1
    assert interval_job_res == res.json["hits"]["hits"][0]


def test_jobs_delete(db, client, jobs):
    """Test job deletion."""
    res = client.delete(f"/jobs/{jobs.simple.id}")
    assert res.status_code == 204

    # Shouldn't be able to get again
    res = client.get(f"/jobs/{jobs.simple.id}")
    assert res.status_code == 404
    assert res.json == {
        "message": f"Job with ID {jobs.simple.id} does not exist.",
        "status": 404,
    }

    # Shouldn't appear in search results
    res = client.get("/jobs")
    assert res.status_code == 200
    assert "hits" in res.json
    assert res.json["hits"]["total"] == 2
    hits = res.json["hits"]["hits"]
    assert all(j["id"] != jobs.simple.id for j in hits)


@pytest.mark.skip()
@patch.object(execute_run, "apply_async")
def test_job_template_args(mock_apply_async, app, db, client, user):
    client = user.login(client)
    job_payload = {
        "title": "Job with template args",
        "task": "tasks.mock_task",
        "default_args": {
            "arg1": "{{ 1 + 1 }}",
            "arg2": "{{ job.title | upper }}",
            "kwarg1": "{{ last_run.created.isoformat() if last_run else None }}",
        },
    }

    # Create a job
    res = client.post("/jobs", json=job_payload)
    assert res.status_code == 201
    job_id = res.json["id"]
    expected_job = {
        "id": job_id,
        "title": "Job with template args",
        "description": None,
        "active": True,
        "task": "tasks.mock_task",
        "default_queue": "celery",
        "default_args": {
            "arg1": "{{ 1 + 1 }}",
            "arg2": "{{ job.title | upper }}",
            "kwarg1": "{{ last_run.created.isoformat() if last_run else None }}",
        },
        "run_args": None,
        "schedule": None,
        "last_run": None,
        "created": res.json["created"],
        "updated": res.json["updated"],
        "links": {
            "self": f"https://127.0.0.1:5000/api/jobs/{job_id}",
            "runs": f"https://127.0.0.1:5000/api/jobs/{job_id}/runs",
        },
    }
    assert res.json == expected_job

    # Create/trigger a run
    res = client.post(f"/jobs/{job_id}/runs")
    assert res.status_code == 201
    run_id = res.json["id"]
    expected_run = {
        "id": run_id,
        "job_id": job_id,
        "task_id": res.json["task_id"],
        "started_by_id": int(user.id),
        "started_by": {
            "id": str(user.id),
            "username": user.username,
            "profile": user._user_profile,
            "links": {
                # "self": f"https://127.0.0.1:5000/api/users/{user.id}",
            },
            "identities": {},
            "is_current_user": True,
            "type": "user",
        },
        "started_at": res.json["started_at"],
        "finished_at": res.json["finished_at"],
        "status": "QUEUED",
        "message": None,
        "title": None,
        "args": {
            "arg1": 2,
            "arg2": "JOB WITH TEMPLATE ARGS",
            "kwarg1": None,
        },
        "queue": "celery",
        "created": res.json["created"],
        "updated": res.json["updated"],
        "links": {
            "self": f"https://127.0.0.1:5000/api/jobs/{job_id}/runs/{run_id}",
            "logs": f"https://127.0.0.1:5000/api/logs/jobs?q={run_id}",
            "stop": f"https://127.0.0.1:5000/api/jobs/{job_id}/runs/{run_id}/actions/stop",
        },
    }
    assert res.json == expected_run
    last_run_created = res.json["created"].replace("+00:00", "")

    # Trigger another run to test the kwarg1 template depending on the last run
    res = client.post(f"/jobs/{job_id}/runs")
    assert res.status_code == 201
    run_id = res.json["id"]
    expected_run = {
        "id": run_id,
        "job_id": job_id,
        "task_id": res.json["task_id"],
        "started_by_id": int(user.id),
        "started_by": {
            "id": str(user.id),
            "username": user.username,
            "profile": user._user_profile,
            "links": {
                # "self": f"https://127.0.0.1:5000/api/users/{user.id}",
            },
            "identities": {},
            "is_current_user": True,
            "type": "user",
        },
        "started_at": res.json["started_at"],
        "finished_at": res.json["finished_at"],
        "status": "QUEUED",
        "message": None,
        "title": None,
        "args": {
            "arg1": 2,
            "arg2": "JOB WITH TEMPLATE ARGS",
            "kwarg1": last_run_created,
        },
        "queue": "celery",
        "created": res.json["created"],
        "updated": res.json["updated"],
        "links": {
            "self": f"https://127.0.0.1:5000/api/jobs/{job_id}/runs/{run_id}",
            "logs": f"https://127.0.0.1:5000/api/logs/jobs?q={run_id}",
            "stop": f"https://127.0.0.1:5000/api/jobs/{job_id}/runs/{run_id}/actions/stop",
        },
    }
    assert res.json == expected_run
