#!/usr/bin/env python3
"""Fail-closed validation of *reported* OCI quiescence evidence.

This is a pure schema/consistency check, NOT a production runtime attestor.
Inventory must come from an independently approved deployment manifest.
An adapter must collect real observations from every process and OCI mutation
entry point; caller-supplied JSON cannot establish production quiescence.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

class QuiescenceRejected(ValueError):
    pass

def _nonbool_int(value):
    return type(value) is int and value >= 0

def validate_quiescence_evidence(evidence, *, expected_writers, expected_scope, now_epoch, max_age_seconds=30):
    """Reject stale, incomplete, ambiguous or non-blocked writer reports.

    This function deliberately never returns production authorization.
    """
    if not isinstance(expected_scope, str) or not expected_scope:
        raise QuiescenceRejected("trusted scope missing")
    if (not isinstance(expected_writers, (tuple, list)) or not expected_writers
        or any(not isinstance(x, str) or not x.strip() for x in expected_writers)
        or len(set(expected_writers)) != len(expected_writers)):
        raise QuiescenceRejected("trusted writer inventory missing or invalid")
    if not isinstance(evidence, Mapping):
        raise QuiescenceRejected("evidence is not an object")
    if set(evidence) != {"scope", "observed_at", "writers"}:
        raise QuiescenceRejected("unrecognized or missing evidence fields")
    if evidence["scope"] != expected_scope:
        raise QuiescenceRejected("scope mismatch")
    observed = evidence["observed_at"]
    if (type(observed) not in (int, float) or not math.isfinite(observed)
        or type(now_epoch) not in (int, float) or not math.isfinite(now_epoch)
        or type(max_age_seconds) not in (int, float) or max_age_seconds <= 0
        or not 0 <= now_epoch - observed <= max_age_seconds):
        raise QuiescenceRejected("invalid, future, or stale observation")
    writers=evidence["writers"]
    if not isinstance(writers,list) or len(writers)!=len(expected_writers):
        raise QuiescenceRejected("missing or extra writer evidence")
    seen=set()
    for item in writers:
        if not isinstance(item,Mapping) or set(item)!={"id","admission_blocked","worker_stopped","in_flight"}:
            raise QuiescenceRejected("malformed writer evidence")
        writer=item["id"]
        if not isinstance(writer,str) or writer not in expected_writers or writer in seen:
            raise QuiescenceRejected("unexpected or duplicate writer")
        seen.add(writer)
        if (item["admission_blocked"] is not True
            or item["worker_stopped"] is not True
            or not _nonbool_int(item["in_flight"])
            or item["in_flight"] != 0):
            raise QuiescenceRejected("OCI writer not proven quiescent")
    if seen != set(expected_writers):
        raise QuiescenceRejected("incomplete writer inventory")
    return {"evidence_consistent": True, "production_authorized": False}
