#!/usr/bin/env python3
"""Tests for Chronos origin routing on platform-event trigger flows."""

import asyncio
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.db.database import Base
from gluesync_scheduler.models.models import ExecutionMode, TaskType, TriggerFlow, TriggerFlowEvent
from gluesync_scheduler.models.trigger_schemas import TriggerEventCreate, TriggerFlowCreate, TriggerRouting
from gluesync_scheduler.services.origin_routing import (
    EventSource,
    ROUTING_BROADCAST,
    ROUTING_ORIGIN,
    default_routing_for_platform_event,
    extract_event_source,
    filter_events_for_routing,
    job_target_matches,
    normalize_routing,
)
from gluesync_scheduler.services.trigger_flow_service import TriggerFlowService


@pytest.fixture(scope="function")
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def _entity_event(entity_id: str, pipeline_id: str = "pipe-1") -> TriggerEventCreate:
    return TriggerEventCreate(
        task_type=TaskType.ENTITY_SNAPSHOT,
        pipeline_id=pipeline_id,
        entity_ids=[entity_id],
        execution_mode="async",
    )


def _ns_event(entity_ids=None, group_ids=None, pipeline_id="pipe-1"):
    return SimpleNamespace(
        entity_ids=json.dumps(entity_ids) if entity_ids is not None else None,
        group_ids=json.dumps(group_ids) if group_ids is not None else None,
        pipeline_id=pipeline_id,
    )


class TestDefaultRouting:
    def test_default_for_entity_scoped_table_reorg_is_origin(self):
        assert default_routing_for_platform_event("TABLE_REORGANIZATION") == ROUTING_ORIGIN

    def test_default_for_table_truncate_is_origin(self):
        assert default_routing_for_platform_event("TABLE_TRUNCATE") == ROUTING_ORIGIN

    def test_default_for_table_ddl_is_origin(self):
        assert default_routing_for_platform_event("TABLE_DDL") == ROUTING_ORIGIN

    def test_default_for_cluster_wide_is_broadcast(self):
        assert default_routing_for_platform_event("ENTITY_CDC_STARTED") == ROUTING_BROADCAST
        assert default_routing_for_platform_event(None) == ROUTING_BROADCAST

    def test_new_binding_persists_origin_default_for_table_reorg(self, db_session):
        svc = TriggerFlowService(db_session)
        data = TriggerFlowCreate(
            name="reorg-loopback",
            platform_event="TABLE_REORGANIZATION",
            events=[_entity_event("table-1")],
        )
        flow, _ = svc.create_flow(data)
        stored = db_session.query(TriggerFlow).filter_by(id=flow.id).first()
        assert stored.routing == ROUTING_ORIGIN
        assert data.routing == TriggerRouting.ORIGIN

    def test_new_binding_persists_broadcast_default_for_cluster_wide(self, db_session):
        svc = TriggerFlowService(db_session)
        data = TriggerFlowCreate(
            name="cdc-fanout",
            platform_event="ENTITY_CDC_STARTED",
            events=[_entity_event("e1")],
        )
        flow, _ = svc.create_flow(data)
        stored = db_session.query(TriggerFlow).filter_by(id=flow.id).first()
        assert stored.routing == ROUTING_BROADCAST


class TestExtractAndMatch:
    def test_extract_event_source_hub_shape(self):
        src = extract_event_source({"source": {"kind": "table", "id": "t-9"}})
        assert src == EventSource(kind="table", id="t-9")

    def test_extract_event_source_entityId_fallback(self):
        src = extract_event_source({"entityId": "e-1"})
        assert src == EventSource(kind="entity", id="e-1")

    def test_job_target_matches_kind_and_id(self):
        ev = _ns_event(entity_ids=["t-9"])
        assert job_target_matches(ev, EventSource(kind="table", id="t-9")) is True
        assert job_target_matches(ev, EventSource(kind="table", id="other")) is False
        assert job_target_matches(ev, EventSource(kind="pipeline", id="pipe-1")) is True
        assert job_target_matches(ev, EventSource(kind="pipeline", id="other-pipe")) is False

    def test_omitted_routing_is_broadcast(self):
        assert normalize_routing(None) == ROUTING_BROADCAST
        evs = [_ns_event(entity_ids=["a"]), _ns_event(entity_ids=["b"])]
        out = filter_events_for_routing(evs, None, {"source": {"kind": "table", "id": "a"}})
        assert out == evs


    def test_extract_nested_event_source(self):
        src = extract_event_source({"event": {"source": {"kind": "group", "id": "g-1"}}})
        assert src == EventSource(kind="group", id="g-1")

    def test_extract_objectId_fallback(self):
        src = extract_event_source({"data": {"objectId": "obj-2"}})
        assert src == EventSource(kind="object", id="obj-2")

    def test_job_target_matches_group(self):
        ev = _ns_event(group_ids=["g-1"])
        assert job_target_matches(ev, EventSource(kind="group", id="g-1")) is True
        assert job_target_matches(ev, EventSource(kind="group", id="g-x")) is False

    def test_unknown_routing_treated_as_broadcast(self):
        evs = [_ns_event(entity_ids=["a"])]
        out = filter_events_for_routing(evs, "sideways", {"source": {"kind": "table", "id": "z"}})
        assert out == evs

    def test_substring_table_reorg_is_entity_scoped(self):
        assert default_routing_for_platform_event("DB2_TABLE_REORG_COMPLETED") == ROUTING_ORIGIN

    def test_normalize_routing_rejects_unknown(self):
        with pytest.raises(ValueError):
            normalize_routing("sideways")



class TestDispatch:
    def _seed_flow(self, db, routing, entity_ids):
        flow = TriggerFlow(
            name="f",
            enabled=True,
            platform_event="TABLE_REORGANIZATION",
            routing=routing,
            secret_token="tok",
        )
        db.add(flow)
        db.flush()
        for i, eid in enumerate(entity_ids):
            db.add(
                TriggerFlowEvent(
                    trigger_flow_id=flow.id,
                    position=i,
                    task_type=TaskType.ENTITY_SNAPSHOT,
                    pipeline_id="pipe-1",
                    entity_ids=json.dumps([eid]),
                    execution_mode=ExecutionMode.ASYNC,
                )
            )
        db.commit()
        return flow.id

    def test_origin_match_runs(self, db_session):
        flow_id = self._seed_flow(db_session, ROUTING_ORIGIN, ["t-match", "t-other"])
        svc = TriggerFlowService(db_session)
        payload = {"source": {"kind": "table", "id": "t-match"}}
        with patch(
            "gluesync_scheduler.services.trigger_flow_service.chain_execution_service.execute_trigger_flow",
            new=AsyncMock(return_value=True),
        ) as exec_mock:
            ok, err = asyncio.run(
                svc.fire(flow_id, source="platform_event", event_payload=payload)
            )
        assert ok is True
        assert err == ""
        ran = exec_mock.await_args.args[1]
        assert len(ran) == 1
        assert json.loads(ran[0].entity_ids) == ["t-match"]

    def test_origin_mismatch_noop(self, db_session):
        flow_id = self._seed_flow(db_session, ROUTING_ORIGIN, ["t-a"])
        svc = TriggerFlowService(db_session)
        payload = {"source": {"kind": "table", "id": "t-other"}}
        with patch(
            "gluesync_scheduler.services.trigger_flow_service.chain_execution_service.execute_trigger_flow",
            new=AsyncMock(return_value=True),
        ) as exec_mock:
            ok, err = asyncio.run(
                svc.fire(flow_id, source="platform_event", event_payload=payload)
            )
        assert ok is True
        exec_mock.assert_not_awaited()

    def test_broadcast_fans_out(self, db_session):
        flow_id = self._seed_flow(db_session, ROUTING_BROADCAST, ["t-a", "t-b"])
        svc = TriggerFlowService(db_session)
        payload = {"source": {"kind": "table", "id": "t-a"}}
        with patch(
            "gluesync_scheduler.services.trigger_flow_service.chain_execution_service.execute_trigger_flow",
            new=AsyncMock(return_value=True),
        ) as exec_mock:
            ok, _ = asyncio.run(
                svc.fire(flow_id, source="platform_event", event_payload=payload)
            )
        assert ok is True
        ran = exec_mock.await_args.args[1]
        assert len(ran) == 2

    def test_omitted_routing_equals_broadcast(self, db_session):
        flow_id = self._seed_flow(db_session, None, ["t-a", "t-b"])
        svc = TriggerFlowService(db_session)
        with patch(
            "gluesync_scheduler.services.trigger_flow_service.chain_execution_service.execute_trigger_flow",
            new=AsyncMock(return_value=True),
        ) as exec_mock:
            asyncio.run(
                svc.fire(
                    flow_id,
                    source="platform_event",
                    event_payload={"source": {"kind": "table", "id": "t-a"}},
                )
            )
        assert len(exec_mock.await_args.args[1]) == 2

    def test_payload_fallback_without_event_source(self, db_session):
        flow_id = self._seed_flow(db_session, ROUTING_ORIGIN, ["legacy-1", "legacy-2"])
        svc = TriggerFlowService(db_session)
        with patch(
            "gluesync_scheduler.services.trigger_flow_service.chain_execution_service.execute_trigger_flow",
            new=AsyncMock(return_value=True),
        ) as exec_mock:
            asyncio.run(
                svc.fire(
                    flow_id,
                    source="platform_event",
                    event_payload={"entityId": "legacy-1"},
                )
            )
        ran = exec_mock.await_args.args[1]
        assert len(ran) == 1
        assert json.loads(ran[0].entity_ids) == ["legacy-1"]

    def test_origin_empty_payload_is_noop(self, db_session):
        flow_id = self._seed_flow(db_session, ROUTING_ORIGIN, ["t-a"])
        svc = TriggerFlowService(db_session)
        with patch(
            "gluesync_scheduler.services.trigger_flow_service.chain_execution_service.execute_trigger_flow",
            new=AsyncMock(return_value=True),
        ) as exec_mock:
            ok, _ = asyncio.run(svc.fire(flow_id, source="platform_event", event_payload={}))
        assert ok is True
        exec_mock.assert_not_awaited()

    def test_update_flow_persists_routing(self, db_session):
        from gluesync_scheduler.models.trigger_schemas import TriggerFlowUpdate
        svc = TriggerFlowService(db_session)
        data = TriggerFlowCreate(
            name="upd",
            platform_event="ENTITY_CDC_STARTED",
            events=[_entity_event("e1")],
        )
        flow, _ = svc.create_flow(data)
        assert flow.routing == ROUTING_BROADCAST
        svc.update_flow(flow.id, TriggerFlowUpdate(routing=TriggerRouting.ORIGIN))
        stored = db_session.query(TriggerFlow).filter_by(id=flow.id).first()
        assert stored.routing == ROUTING_ORIGIN

