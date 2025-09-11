# -*- coding: utf-8 -*-
#
# Copyright (C) 2024 CERN.
# Copyright (C) 2025 Graz University of Technology.
#
# Invenio-Jobs is free software; you can redistribute it and/or modify it
# under the terms of the MIT License; see LICENSE file for more details.

"""Service schemas."""

import inspect
import json
from copy import deepcopy
from datetime import datetime, timezone

from dateutil.parser import isoparse
from invenio_i18n import lazy_gettext as _
from invenio_users_resources.services import schemas as user_schemas
from marshmallow import EXCLUDE, Schema, fields, post_load, pre_dump, pre_load, validate
from marshmallow_oneofschema import OneOfSchema
from marshmallow_utils.fields import SanitizedUnicode, TZDateTime
from marshmallow_utils.permissions import FieldPermissionsMixin
from marshmallow_utils.validators import LazyOneOf

from ..models import RunStatusEnum, Task
from ..proxies import current_jobs


def _not_blank(**kwargs):
    """Returns a non-blank validation rule."""
    max_ = kwargs.get("max", "")
    return validate.Length(
        error=_(
            "Field cannot be blank or longer than {max_} characters.".format(max_=max_)
        ),
        min=1,
        **kwargs,
    )


class TaskParameterSchema(Schema):
    """Schema for a task parameter."""

    name = SanitizedUnicode()

    # TODO: Make custom schema for serializing parameter types
    default = fields.Method("dump_default")
    kind = fields.String()

    def dump_default(self, obj):
        """Dump the default value."""
        if obj.default in (None, inspect.Parameter.empty):
            return None
        elif isinstance(obj.default, (bool, int, float, str)):
            return obj.default
        else:
            return str(obj.default)


class TaskSchema(Schema, FieldPermissionsMixin):
    """Schema for a task."""

    name = SanitizedUnicode()
    description = SanitizedUnicode()
    parameters = fields.Dict(
        keys=SanitizedUnicode(),
        values=fields.Nested(TaskParameterSchema),
    )


class IntervalScheduleSchema(Schema):
    """Schema for an interval schedule based on ``datetime.timedelta``."""

    type = fields.Constant("interval")

    days = fields.Integer()
    seconds = fields.Integer()
    microseconds = fields.Integer()
    milliseconds = fields.Integer()
    minutes = fields.Integer()
    hours = fields.Integer()
    weeks = fields.Integer()


class CrontabScheduleSchema(Schema):
    """Schema for a crontab schedule."""

    type = fields.Constant("crontab")

    minute = fields.String(load_default="*")
    hour = fields.String(load_default="*")
    day_of_week = fields.String(load_default="*")
    day_of_month = fields.String(load_default="*")
    month_of_year = fields.String(load_default="*")


class CustomArgsSchema(Schema):
    """Custom arguments schema."""

    args = fields.Raw(load_default=dict, allow_none=True)


class ScheduleSchema(OneOfSchema):
    """Schema for a schedule."""

    def get_obj_type(self, obj):
        """Get type from object data."""
        if isinstance(obj, dict) and "type" in obj:
            return obj["type"]

    type_schemas = {
        "interval": IntervalScheduleSchema,
        "crontab": CrontabScheduleSchema,
    }
    type_field_remove = False


class JobArgumentsSchema(OneOfSchema):
    """Base schema for tasks with arguments."""

    type_field_remove = False
    type_field = "job_arg_schema"

    def __init__(self, *args, **kwargs):
        """Constructor."""
        self.type_schemas = deepcopy(current_jobs.registry.schemas)
        self.type_schemas["custom"] = CustomArgsSchema
        super().__init__(*args, **kwargs)

    def get_obj_type(self, obj):
        """Return object type."""
        if isinstance(obj, dict) and "job_arg_schema" in obj:
            return obj["job_arg_schema"]
        if isinstance(obj, dict) and "job_arg_schema" not in obj:
            return "custom"

    def get_data_type(self, data):
        """Get data type. Defaults to custom if no type is provided."""
        data_type = super().get_data_type(data)
        if data_type is None:
            return "custom"
        else:
            return data_type


class JobSchema(Schema, FieldPermissionsMixin):
    """Base schema for a job."""

    class Meta:
        """Meta attributes for the schema."""

        unknown = EXCLUDE

    id = fields.UUID(dump_only=True)

    created = TZDateTime(timezone=timezone.utc, format="iso", dump_only=True)
    updated = TZDateTime(timezone=timezone.utc, format="iso", dump_only=True)

    title = SanitizedUnicode(required=True, validate=_not_blank(max=250))
    description = SanitizedUnicode()

    active = fields.Boolean(load_default=True)

    task = fields.String(
        required=True,
        validate=LazyOneOf(choices=lambda: [name for name, t in Task.all().items()]),
    )
    default_queue = fields.String(
        validate=LazyOneOf(choices=lambda: current_jobs.queues.keys()),
        load_default=lambda: current_jobs.default_queue,
    )

    default_args = fields.Raw(dump_only=True, dump_default=dict, load_default=dict)
    run_args = fields.Dict(allow_none=True)

    schedule = fields.Nested(ScheduleSchema, allow_none=True, load_default=None)

    last_run = fields.Nested(lambda: RunSchema, dump_only=True)
    last_runs = fields.Raw(dump_only=True)

    @pre_dump
    def dump_last_runs(self, obj, many=False, **kwargs):
        """Dump last runs of a job."""
        last_runs = obj.get("last_runs", {})
        for key, value in last_runs.items():
            if value:
                last_runs[key] = RunSchema().dump(value.dump())
        return obj

    @pre_load
    def args_adapter(self, obj, many, **kwargs):
        """Adapt UI schema args to the service schema."""
        if "args" in obj:
            args = obj.pop("args")
            obj["run_args"] = {
                **args,
            }
            custom_args = json.loads(obj.pop("custom_args", "{}"))
            if custom_args:
                obj["run_args"]["custom_args"] = custom_args
        return obj


class JobEditSchema(JobSchema):
    """Schema for Job Edit."""

    task = fields.String(
        dump_only=True,
        validate=LazyOneOf(choices=lambda: [name for name, t in Task.all().items()]),
    )
    args = fields.Nested(
        lambda: JobArgumentsSchema,
        metadata={
            "type": "dynamic",
            "endpoint": "/api/tasks/<item_id>/args",
            "depends_on": "task",
        },
    )
    custom_args = fields.Raw(
        load_default=dict,
        allow_none=True,
        metadata={
            "title": "Custom args",
            "description": "Advanced configuration for seasoned administrators.",
        },
    )


class UserSchema(OneOfSchema):
    """User schema."""

    def get_obj_type(self, obj):
        """Get type from object data."""
        return "system" if obj is None else "user"

    type_schemas = {
        "user": user_schemas.UserSchema,
        "system": user_schemas.SystemUserSchema,
    }


class RunSchema(Schema, FieldPermissionsMixin):
    """Base schema for a job run."""

    class Meta:
        """Meta attributes for the schema."""

        unknown = EXCLUDE

    id = fields.UUID(dump_only=True)
    job_id = fields.UUID(dump_only=True)

    created = TZDateTime(timezone=timezone.utc, format="iso", dump_only=True)
    updated = TZDateTime(timezone=timezone.utc, format="iso", dump_only=True)

    started_by_id = fields.Integer(dump_only=True)
    started_by = fields.Nested(UserSchema, dump_only=True)

    started_at = TZDateTime(timezone=timezone.utc, format="iso", dump_only=True)
    finished_at = TZDateTime(timezone=timezone.utc, format="iso", dump_only=True)

    status = fields.Enum(RunStatusEnum, dump_only=True)
    message = SanitizedUnicode(dump_only=True)

    task_id = fields.UUID(dump_only=True)

    parent_run_id = fields.UUID(
        dump_only=True,
        allow_none=True,
        metadata={
            "description": "ID of the parent run if this is a subtask.",
            "title": "Parent Run ID",
        },
    )
    subtasks = fields.List(
        fields.Nested(lambda: RunSchema(exclude=("subtasks",))),
        dump_only=True,
        metadata={"description": "List of subtasks for this run."},
    )

    total_subtasks = fields.Integer(dump_only=True, default=0)
    completed_subtasks = fields.Integer(dump_only=True, default=0)
    failed_subtasks = fields.Integer(dump_only=True, default=0)
    errored_entries = fields.Integer(
        dump_only=True,
        default=0,
        metadata={
            "description": "Number of entries that failed during processing.",
            "title": "Errored Entries",
        },
    )
    total_entries = fields.Integer(
        dump_only=True,
        default=0,
        metadata={
            "description": "Total number of entries processed by this run.",
            "title": "Total Entries",
        },
    )

    # Input fields
    title = SanitizedUnicode(validate=_not_blank(max=250), dump_default="Manual run")
    args = fields.Nested(
        lambda: JobArgumentsSchema,
        metadata={
            "type": "dynamic",
            "endpoint": "/api/tasks/<item_id>/args",
            "depends_on": "task",
        },
    )
    custom_args = fields.Raw(
        load_default=dict,
        allow_none=True,
        metadata={
            "title": "Custom args",
            "description": "Advanced configuration for seasoned administrators.",
        },
    )
    queue = fields.String(
        validate=LazyOneOf(choices=lambda: current_jobs.queues.keys()),
    )

    @post_load
    def load_custom_args(self, obj, many, **kwargs):
        """Load custom args if present."""
        custom_args = obj.pop("custom_args")
        if custom_args:
            obj["args"]["custom_args"] = json.loads(custom_args)
        return obj


class LogContextSchema(Schema):
    """Schema for the job context with required job_id and dynamic fields."""

    job_id = fields.Str(required=True)
    run_id = fields.Str(required=True)
    identity_id = fields.Str(required=True)


class JobLogEntrySchema(Schema):
    """Schema for structured OpenSearch job log entries."""

    timestamp = fields.DateTime(attribute="@timestamp", required=True)
    level = fields.Str(
        required=True,
        validate=validate.OneOf(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
    )
    message = fields.Str(required=True)
    module = fields.Str(required=True)
    function = fields.Str(required=True)
    line = fields.Int(required=True)
    context = fields.Nested(LogContextSchema, required=True)
    sort = fields.List(fields.Field, dump_only=True)

    def dump(self, obj, **kwargs):
        """Ensure @timestamp is a datetime object and serialize properly."""
        ts = getattr(obj, "@timestamp", None)

        if isinstance(ts, str):
            setattr(obj, "@timestamp", isoparse(ts))
        elif ts is None:
            raise ValueError("Missing '@timestamp' field")

        return super().dump(obj, **kwargs)
