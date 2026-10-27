"""在线轨迹（FR-09）：阶段/状态必须落在契约枚举内，摘要只写结论。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.agents.trace import MAX_SUMMARY, STAGES, STATUSES, TraceRecorder


def recorder() -> TraceRecorder:
    return TraceRecorder(trace_id="trace-1", model_or_rule_version="rule:criteria@1")


def test_sequence_follows_the_call_order_and_versions_are_shared():
    trace = recorder()
    first = trace.add("profile", input_summary="读取画像", output_summary="12 条事实")
    second = trace.add("evaluation", input_summary="求值", output_summary="3 项判定")

    assert (first.sequence, second.sequence) == (1, 2)
    assert {step.model_or_rule_version for step in trace.steps} == {"rule:criteria@1"}


def test_steps_snapshot_is_a_copy():
    trace = recorder()
    trace.add("profile", input_summary="a", output_summary="b")

    snapshot = trace.steps
    snapshot.clear()

    assert len(trace.steps) == 1


def test_unknown_stage_or_status_is_rejected():
    trace = recorder()

    with pytest.raises(ValueError):
        trace.add("llm_thinking", input_summary="a", output_summary="b")
    with pytest.raises(ValueError):
        trace.add("profile", status="succeeded", input_summary="a", output_summary="b")
    assert trace.steps == []


@pytest.mark.parametrize("stage", STAGES)
def test_every_contract_stage_can_be_recorded(stage):
    assert recorder().add(stage, input_summary="a", output_summary="b").stage == stage


@pytest.mark.parametrize("status", STATUSES)
def test_every_contract_status_can_be_recorded(status):
    step = recorder().add("profile", status=status, input_summary="a", output_summary="b")
    assert step.status == status


def test_summary_is_collapsed_and_clipped():
    step = recorder().add(
        "profile",
        input_summary="  多行\n摘要\t带空白  ",
        output_summary="x" * (MAX_SUMMARY + 50),
    )

    assert step.input_summary == "多行 摘要 带空白"
    assert len(step.output_summary) == MAX_SUMMARY
    assert step.output_summary.endswith("…")


def test_wire_shape_uses_camel_case_and_stringifies_ids():
    policy_id = uuid4()
    step = recorder().add(
        "human_review",
        input_summary="审核",
        output_summary="通过",
        evidence_refs=[{"type": "policyVersion", "id": str(policy_id)}],
        policy_id=policy_id,
        policy_version="v1.0",
        human_action="approve",
    )

    wire = step.to_wire()

    assert wire["policyId"] == str(policy_id)
    assert wire["policyVersion"] == "v1.0"
    assert wire["humanAction"] == "approve"
    assert wire["evidenceRefs"] == [{"type": "policyVersion", "id": str(policy_id)}]
    assert wire["errorCode"] is None
    assert set(wire) == {
        "sequence",
        "stage",
        "status",
        "inputSummary",
        "outputSummary",
        "evidenceRefs",
        "policyId",
        "policyVersion",
        "modelOrRuleVersion",
        "elapsedMs",
        "errorCode",
        "humanAction",
    }
