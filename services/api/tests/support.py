"""无数据库测试用的假仓储、假对象存储与请求构造工具。"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.agents.contracts import PolicySnapshot, SnapshotCriterion
from app.agents.profile import (
    NormalizedFact,
    NormalizedProfile,
    fact_version_of,
    to_wire_fact,
)
from app.domain.rules import CriterionRule
from app.errors import ApiProblem
from app.schemas import (
    Criterion,
    Enterprise,
    Period,
    PolicyDetail,
    PolicyDraft,
    PolicyDraftList,
    PolicyList,
    PolicySummary,
    PreparationTemplate,
    SourceSpan,
    TraceResponse,
    TraceStep,
    ValidationFinding,
)

ADMIN_TOKEN = "local-dev-admin-token-0001"
DEMO_SESSION_SECRET = "local-dev-demo-session-secret-000000000001"
REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO_ROOT / "contracts" / "openapi.yaml"

OFFICIAL_SOURCE_URL = "https://www.sz.gov.cn/zwgk/zfxxgkml/zcfg/content/post_1.html"
OFFICIAL_ENTRY_URL = "https://www.sz.gov.cn/"

#: 数据库用例的独立 schema 与迁移清单（conftest 与各 db 用例共用）。
SCHEMA = f"huice_test_{os.getpid()}"
MIGRATIONS = sorted((REPO_ROOT / "infra" / "sql").glob("*.sql"))


def with_schema(dsn: str, schema: str) -> str:
    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}options=-csearch_path%3D{schema}"


class FakeObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put(self, data: bytes, *, suffix: str = "") -> str:
        key = f"local/{uuid4().hex}{suffix}"
        self.objects[key] = data
        return key

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.deleted.append(key)


class FakeRepository:
    """内存版仓储，只复述入参，不模拟 SQL 行为。"""

    def __init__(self) -> None:
        self.drafts: list[PolicyDraft] = []
        self.created: list[dict] = []
        self.list_calls: list[dict] = []
        self.get_calls: list[tuple] = []
        self.saved_criteria: list[dict] = []
        self.published: list[PolicyDraft] = []
        self.idempotency: dict[tuple[str, object], dict] = {}
        self.error: Exception | None = None
        # 公开政策、企业画像与判断运行（在线链路的无数据库替身）。
        self.public_list_calls: list[dict] = []
        self.snapshot_calls: list[list] = []
        self.enterprises: dict[object, Enterprise] = {}
        self.normalized: dict[object, tuple[str, Period, list[NormalizedFact]]] = {}
        self.saved_runs: list[dict] = []
        self.runs: dict[object, object] = {}
        self.trace_calls: list[object] = []

    def create_policy_version(
        self,
        *,
        metadata,
        sources,
        storage_keys,
        trace_id,
        started_at=None,
    ) -> PolicyDraft:
        if self.error is not None:
            raise self.error
        draft = build_draft(metadata, sources)
        self.created.append(
            {
                "metadata": metadata,
                "sources": list(sources),
                "storage_keys": list(storage_keys),
                "trace_id": trace_id,
                "started_at": started_at,
            }
        )
        self.drafts.append(draft)
        return draft

    def list_admin_policies(self, *, status, policy_type, cursor, limit) -> PolicyDraftList:
        self.list_calls.append(
            {"status": status, "policy_type": policy_type, "cursor": cursor, "limit": limit}
        )
        items = [
            draft
            for draft in self.drafts
            if (status is None or draft.status == status)
            and (policy_type is None or draft.policy_type == policy_type)
        ]
        return PolicyDraftList(items=items[:limit], next_cursor=None)

    def get_admin_policy(self, policy_id, version) -> PolicyDraft:
        self.get_calls.append((policy_id, version))
        for draft in reversed(self.drafts):
            if draft.id == policy_id and (version is None or draft.version == version):
                return draft
        raise ApiProblem(404, "POLICY_NOT_FOUND", "没有找到对应的政策草稿。")

    # ------------------------------------------------------- 条款与发布

    def get_version_for_edit(self, policy_id, version) -> PolicyDraft:
        for draft in reversed(self.drafts):
            if draft.id == policy_id and draft.version == version:
                return draft
        raise ApiProblem(404, "POLICY_NOT_FOUND", "没有找到对应的政策版本。")

    def save_criteria(
        self,
        *,
        policy_id,
        version,
        expected_revision,
        criteria,
        findings,
        status,
        trace_id,
        started_at=None,
    ) -> PolicyDraft:
        draft = self.get_version_for_edit(policy_id, version)
        self._assert_editable(draft, expected_revision)
        self.saved_criteria.append({"criteria": list(criteria), "findings": list(findings), "status": status})
        updated = draft.model_copy(
            update={
                "criteria": [
                    Criterion(
                        id=record.id,
                        title=record.title,
                        expression=record.expression,
                        source_spans=[SourceSpan.model_validate(span) for span in record.spans],
                        review_finding=record.review_finding,
                        validation_status=record.validation_status,
                    )
                    for record in criteria
                ],
                "validation_findings": [
                    ValidationFinding(
                        criterion_id=record.criterion_id,
                        code=record.code,
                        message=record.message,
                        source_spans=[SourceSpan.model_validate(span) for span in record.spans],
                    )
                    for record in findings
                ],
                "status": status,
                "revision": uuid4(),
            }
        )
        self._replace(draft, updated)
        return updated

    def publish(self, *, policy_id, version, expected_revision, trace_id, started_at=None) -> PolicyDraft:
        draft = self.get_version_for_edit(policy_id, version)
        if draft.status in ("published", "closed"):
            raise ApiProblem(
                409,
                "POLICY_ALREADY_PUBLISHED",
                "该版本已发布，不能重复发布。",
                {"currentVersion": str(draft.revision)},
            )
        self._assert_editable(draft, expected_revision)
        if not draft.criteria:
            raise ApiProblem(422, "CRITERIA_REQUIRED", "该版本还没有条款，不能发布。")
        blocked = [str(item.id) for item in draft.criteria if item.validation_status != "passed"]
        if blocked:
            raise ApiProblem(
                422, "CRITERIA_NOT_VALIDATED", "存在引用校验未通过的条件，不能发布。", {"criterionIds": blocked}
            )
        if not draft.sources:
            raise ApiProblem(422, "SOURCE_REQUIRED", "该版本没有已保存的原文，不能发布。")
        updated = draft.model_copy(update={"status": "published", "revision": uuid4()})
        self._replace(draft, updated)
        self.published.append(updated)
        return updated

    def get_policy_detail(self, policy_id, version=None):
        for draft in reversed(self.drafts):
            if draft.id == policy_id and draft.status == "published":
                if version is None or draft.version == version:
                    return PolicyDetail(
                        id=draft.id,
                        title=draft.title,
                        policy_type=draft.policy_type,
                        issuer=draft.issuer,
                        region=draft.region,
                        version=draft.version,
                        status="published",
                        effective_from=draft.effective_from,
                        effective_to=draft.effective_to,
                        application_window=draft.application_window,
                        source_url=draft.source_url,
                        document_number=draft.document_number,
                        applicable_entities=draft.applicable_entities,
                        published_at=draft.published_at,
                        official_entry_url=draft.official_entry_url,
                        sources=draft.sources,
                        criteria=draft.criteria,
                        preparation_template=PreparationTemplate(
                            policy_type=draft.policy_type,
                            handling_mode="tax_self_assessment"
                            if draft.policy_type == "tax"
                            else "grant_application",
                            official_entry_url=draft.official_entry_url,
                            deadline=draft.application_window.closes_at
                            if draft.application_window
                            else None,
                            items=[],
                        ),
                    )
        raise ApiProblem(404, "POLICY_NOT_FOUND", "没有找到对应的已发布政策。")

    def read_idempotent(self, scope: str, key):
        return self.idempotency.get((scope, key))

    def save_idempotent(self, scope: str, key, fingerprint: str, response: dict, revision) -> None:
        self.idempotency.setdefault(
            (scope, key), {"fingerprint": fingerprint, "response": response, "revision": revision}
        )

    # ------------------------------------------------------------ 公开政策

    def list_public_policies(
        self, *, status, policy_type, region, as_of, cursor, limit
    ) -> PolicyList:
        self.public_list_calls.append(
            {
                "status": status,
                "policy_type": policy_type,
                "region": region,
                "as_of": as_of,
                "cursor": cursor,
                "limit": limit,
            }
        )
        items: list[PolicySummary] = []
        for draft in self.drafts:
            if draft.status not in ("published", "closed"):
                continue
            if status is not None and draft.status != status:
                continue
            if policy_type is not None and draft.policy_type != policy_type:
                continue
            if region is not None and draft.region != region:
                continue
            items.append(
                PolicySummary(
                    id=draft.id,
                    title=draft.title,
                    policy_type=draft.policy_type,
                    issuer=draft.issuer,
                    region=draft.region,
                    version=draft.version,
                    status=draft.status,
                    effective_from=draft.effective_from,
                    effective_to=draft.effective_to,
                    application_window=draft.application_window,
                    source_url=draft.source_url,
                )
            )
        return PolicyList(items=items[:limit], next_cursor=None)

    # -------------------------------------------------------------- 企业画像

    def create_enterprise(self, *, profile: NormalizedProfile, trace_id: str) -> Enterprise:
        enterprise_id = uuid4()
        self.created.append({"profile": profile, "trace_id": trace_id})
        return self._store_enterprise(
            enterprise_id,
            profile.name_synthetic,
            Period(start_date=profile.period_start, end_date=profile.period_end),
            list(profile.facts),
        )

    def get_enterprise(self, enterprise_id) -> Enterprise:
        enterprise = self.enterprises.get(enterprise_id)
        if enterprise is None:
            raise ApiProblem(404, "ENTERPRISE_NOT_FOUND", "没有找到对应的企业画像。")
        return enterprise

    def update_fact(
        self, *, enterprise_id, fact: NormalizedFact, expected_fact_version, trace_id
    ) -> Enterprise:
        current = self.get_enterprise(enterprise_id)
        if expected_fact_version != current.fact_version:
            raise ApiProblem(
                409,
                "VERSION_CONFLICT",
                "画像已被他人更新，请重新读取后再提交。",
                {"currentVersion": current.fact_version},
            )
        name, period, facts = self.normalized[enterprise_id]
        replaced = [item for item in facts if item.ref == fact.ref]
        if replaced:
            facts = [fact if item.ref == fact.ref else item for item in facts]
        else:
            facts = [*facts, fact]
        return self._store_enterprise(enterprise_id, name, period, facts)

    def _store_enterprise(
        self, enterprise_id, name: str, period: Period, facts: list[NormalizedFact]
    ) -> Enterprise:
        ordered = sorted(facts, key=lambda item: (item.key, item.period_start, item.period_end))
        enterprise = Enterprise(
            id=enterprise_id,
            name_synthetic=name,
            period=period,
            facts=[to_wire_fact(fact) for fact in ordered],
            fact_version=fact_version_of(ordered),
        )
        self.enterprises[enterprise_id] = enterprise
        self.normalized[enterprise_id] = (name, period, ordered)
        return enterprise

    # ------------------------------------------------------------ 判断运行

    def published_policy_snapshots(self, policy_ids) -> list[PolicySnapshot]:
        self.snapshot_calls.append(list(policy_ids))
        snapshots: list[PolicySnapshot] = []
        missing: list[str] = []
        for policy_id in policy_ids:
            draft = self._latest_published(policy_id)
            if draft is None or not draft.criteria:
                missing.append(str(policy_id))
                continue
            snapshots.append(_snapshot_of(draft))
        if missing:
            raise ApiProblem(
                422,
                "POLICY_NOT_PUBLISHED",
                "存在尚未审核发布的政策，不能参与判断。",
                {"policyIds": missing},
            )
        return snapshots

    def _latest_published(self, policy_id) -> PolicyDraft | None:
        for draft in reversed(self.drafts):
            if draft.id == policy_id and draft.status == "published":
                return draft
        return None

    def get_run_header(self, run_id):
        run = self.runs.get(run_id)
        if run is None:
            return None
        return {
            "enterprise_id": run.enterprise_id,
            "period_start": run.period.start_date,
            "period_end": run.period.end_date,
            "policy_ids": [item.policy_id for item in run.assessments],
        }

    def save_run(self, *, run, dependencies, trace_steps, trace_id, policy_version_ids) -> None:
        self.saved_runs.append(
            {
                "run": run,
                "dependencies": dict(dependencies),
                "trace_steps": list(trace_steps),
                "trace_id": trace_id,
                "policy_version_ids": dict(policy_version_ids),
            }
        )
        self.runs[run.id] = run

    def get_run(self, run_id):
        run = self.runs.get(run_id)
        if run is None:
            raise ApiProblem(404, "RUN_NOT_FOUND", "没有找到对应的判断运行。")
        return run

    def get_trace(self, run_id):
        """把保存 run 时收到的在线轨迹原样转成 API-14 的形状。"""

        saved = next((item for item in self.saved_runs if item["run"].id == run_id), None)
        if saved is None:
            raise ApiProblem(404, "RUN_NOT_FOUND", "没有找到对应的判断运行。")
        self.trace_calls.append(run_id)
        steps = [
            TraceStep(
                sequence=index,
                stage=step.stage,
                status=step.status,
                input_summary=step.input_summary,
                output_summary=step.output_summary,
                evidence_refs=list(step.evidence_refs),
                policy_id=None,
                policy_version=None,
                model_or_rule_version=step.model_or_rule_version,
                elapsed_ms=step.elapsed_ms,
                error_code=step.error_code,
                human_action=step.human_action,
            )
            for index, step in enumerate(saved["trace_steps"], start=1)
        ]
        return TraceResponse(steps=steps)

    # ---------------------------------------------------------------- 内部

    @staticmethod
    def _assert_editable(draft: PolicyDraft, expected_revision) -> None:
        if draft.status in ("published", "closed"):
            raise ApiProblem(
                409,
                "POLICY_ALREADY_PUBLISHED",
                "已发布的版本不能再修改。",
                {"currentVersion": str(draft.revision)},
            )
        if expected_revision is not None and draft.revision != expected_revision:
            raise ApiProblem(
                409,
                "VERSION_CONFLICT",
                "数据已被他人更新，请重新读取后再提交。",
                {"currentVersion": str(draft.revision)},
            )

    def _replace(self, old: PolicyDraft, new: PolicyDraft) -> None:
        for index, draft in enumerate(self.drafts):
            if draft is old:
                self.drafts[index] = new
                return
        raise AssertionError("待替换的草稿不在仓储中")


def build_draft(metadata, sources) -> PolicyDraft:
    now = datetime.now(timezone.utc)
    return PolicyDraft.model_validate(
        {
            "id": str(uuid4()),
            "version": metadata.version,
            "title": metadata.title,
            "type": metadata.policy_type,
            "issuer": metadata.issuer,
            "region": metadata.region,
            "documentNumber": metadata.document_number,
            "applicableEntities": list(metadata.applicable_entities),
            "publishedAt": metadata.published_at.isoformat(),
            "effectiveFrom": metadata.effective_from.isoformat(),
            "effectiveTo": metadata.effective_to.isoformat() if metadata.effective_to else None,
            "applicationWindow": {
                "opensAt": metadata.application_window.opens_at.isoformat(),
                "closesAt": metadata.application_window.closes_at.isoformat(),
            }
            if metadata.application_window
            else None,
            "sourceUrl": metadata.source_url,
            "officialEntryUrl": metadata.official_entry_url,
            "status": "pending_parse",
            "revision": str(uuid4()),
            "sources": [
                {
                    "id": str(uuid4()),
                    "kind": source.kind,
                    "sourceUrl": metadata.source_url,
                    "title": source.file_name,
                    "fetchedAt": now.isoformat(),
                    "contentHash": source.content_hash,
                    "text": source.text,
                }
                for source in sources
            ],
            "criteria": [],
            "validationFindings": [],
        }
    )


def metadata_payload(**overrides) -> dict:
    payload = {
        "version": "v1.0",
        "title": "深圳市中小企业数字化转型扶持计划",
        "type": "grant",
        "issuer": "深圳市工业和信息化局",
        "region": "440300",
        "documentNumber": None,
        "applicableEntities": ["中小企业"],
        "publishedAt": "2026-09-01",
        "effectiveFrom": "2026-10-01",
        "effectiveTo": None,
        "applicationWindow": None,
        "sourceUrl": OFFICIAL_SOURCE_URL,
        "officialEntryUrl": OFFICIAL_ENTRY_URL,
    }
    payload.update(overrides)
    return payload


def multipart(metadata, files=None, fields=None) -> list[tuple[str, tuple]]:
    """构造 multipart 请求体；metadata 传字符串可测试坏 JSON。"""
    parts: list[tuple[str, tuple]] = []
    if metadata is not None:
        body = metadata if isinstance(metadata, str) else json.dumps(metadata, ensure_ascii=False)
        parts.append(("metadata", (None, body, "application/json")))
    for name, content, content_type in files or []:
        parts.append(("files", (name, content, content_type)))
    for name, value in fields or []:
        parts.append((name, (None, value, "text/plain")))
    return parts


# ------------------------------------------------------------ 已发布政策构造


def published_draft(*, policy_id=None, expressions=None, **overrides) -> PolicyDraft:
    """构造一份可直接参与判断的已发布版本（条款全部校验通过）。"""

    meta = metadata_payload(**overrides)
    now = datetime.now(timezone.utc)
    source_id = uuid4()
    demo_quote = "此处仅为接口结构演示，不是真实政策。"
    criteria = [
        {
            "id": str(uuid4()),
            "title": f"演示条件 {index + 1}",
            "expression": expression,
            "sourceSpans": [
                {
                    "sourceId": str(source_id),
                    "sourceUrl": OFFICIAL_SOURCE_URL,
                    "sourceVersion": meta["version"],
                    "page": None,
                    "section": "合成演示条款",
                    "quote": demo_quote,
                    "textHash": hashlib.sha256(demo_quote.encode("utf-8")).hexdigest(),
                }
            ],
            "reviewFinding": "none",
            "validationStatus": "passed",
        }
        for index, expression in enumerate(expressions or [])
    ]
    return PolicyDraft.model_validate(
        {
            "id": str(policy_id or uuid4()),
            "version": meta["version"],
            "title": meta["title"],
            "type": meta["type"],
            "issuer": meta["issuer"],
            "region": meta["region"],
            "documentNumber": meta["documentNumber"],
            "applicableEntities": meta["applicableEntities"],
            "publishedAt": meta["publishedAt"],
            "effectiveFrom": meta["effectiveFrom"],
            "effectiveTo": meta["effectiveTo"],
            "applicationWindow": meta["applicationWindow"],
            "sourceUrl": meta["sourceUrl"],
            "officialEntryUrl": meta["officialEntryUrl"],
            "status": "published",
            "revision": str(uuid4()),
            "sources": [
                {
                    "id": str(uuid4()),
                    "kind": "body",
                    "sourceUrl": meta["sourceUrl"],
                    "title": "演示正文",
                    "fetchedAt": now.isoformat(),
                    "contentHash": "0" * 64,
                    "text": "此处仅为接口结构演示，不是真实政策。",
                }
            ],
            "criteria": criteria,
            "validationFindings": [],
        }
    )


def _snapshot_of(draft: PolicyDraft) -> PolicySnapshot:
    window = draft.application_window
    return PolicySnapshot(
        id=draft.id,
        version_id=uuid5(NAMESPACE_URL, f"{draft.id}:{draft.revision}"),
        version=draft.version,
        title=draft.title,
        policy_type=draft.policy_type,
        official_entry_url=draft.official_entry_url,
        effective_to=draft.effective_to,
        window_closes_at=window.closes_at if window else None,
        criteria=tuple(
            SnapshotCriterion(
                rule=CriterionRule(
                    id=item.id,
                    title=item.title,
                    expression=dict(item.expression or {}),
                    review_finding=item.review_finding,
                ),
                spans=tuple(item.source_spans),
            )
            for item in draft.criteria
        ),
    )
