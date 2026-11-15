// Generated from openapi.e2-draft.yaml 0.2.0-draft.3; replace when the formal contract is released.
import type { ApplicationWindow, Criterion, CriterionDraft, Date, ModelUsage, OfficialUrl, PolicyType, Timestamp, UUID, ValidationFinding } from "./types";

export type DiscoveryLimits = {
  minIntervalMinutes: number;
  maxIntervalMinutes: number;
  maxSourceUrls: number;
  maxCandidatesPerRun: number;
  runTimeoutMinutes: number;
  runTokenBudget: number;
  candidateTokenBudget: number;
  maxResponseBytes: number;
  minHostIntervalSeconds: number;
  maxRedirects: number;
};

export type DiscoverySettings = {
  enabled: boolean;
  sourceUrls: Array<OfficialUrl>;
  intervalMinutes: number;
  timeZone: string;
  nextRunAt: (Timestamp) | (null);
  limits: DiscoveryLimits;
};

export type DiscoverySettingsUpdate = {
  enabled: boolean;
  sourceUrls: Array<OfficialUrl>;
  intervalMinutes: number;
  timeZone: string;
};

export type DiscoverySourceStatus = {
  url: OfficialUrl;
  status: "ok" | "failed" | "skipped";
  links?: number;
  candidates?: number;
  articleFailures?: number;
  errorCategory?: (string) | (null);
  message?: (string) | (null);
};

export type DiscoveryRun = {
  id: UUID;
  startedAt: Timestamp;
  finishedAt: (Timestamp) | (null);
  status: "running" | "succeeded" | "partial_failed" | "failed";
  terminalReason: ("completed" | "no_new_policy" | "fetch_failed" | "partial_failed" | "budget_exceeded" | "oversize" | "timeout" | "lock_lost") | (null);
  sourcesChecked: number;
  linksSeen: number;
  duplicates: number;
  suspectedDuplicates: number;
  newCandidates: number;
  failedSources: number;
  failedArticles: number;
  tokensUsed: number;
  tokensUnknown: boolean;
  lateBySeconds: (number) | (null);
  sourceStatus: Array<DiscoverySourceStatus>;
  detail: (string) | (null);
};

export type DiscoveryRunList = {
  items: Array<DiscoveryRun>;
  nextCursor: (string) | (null);
};

export type CandidateStatus = "pending_review" | "extraction_failed" | "submitted" | "discarded" | "expired";

export type CandidateMetadata = {
  version?: (string) | (null);
  policyId?: (UUID) | (null);
  title?: (string) | (null);
  type?: (PolicyType) | (null);
  issuer?: (string) | (null);
  region?: (string) | (null);
  documentNumber?: (string) | (null);
  applicableEntities?: (Array<string>) | (null);
  publishedAt?: (Date) | (null);
  effectiveFrom?: (Date) | (null);
  effectiveTo?: (Date) | (null);
  applicationWindow?: (ApplicationWindow) | (null);
  officialEntryUrl?: (OfficialUrl) | (null);
};

export type CandidateSummary = {
  id: UUID;
  runId: UUID;
  revision: UUID;
  status: CandidateStatus;
  sourceUrl: OfficialUrl;
  fetchedAt: Timestamp;
  contentHash: string;
  title: (string) | (null);
  type?: (PolicyType) | (null);
  issuer: (string) | (null);
  missingFields: Array<string>;
  attachmentCount: number;
  unsupportedAttachmentCount: number;
  duplicateOf: (UUID) | (null);
  suspectedDuplicateOf: (UUID) | (null);
  suspectedDuplicateSourceUrl: (OfficialUrl) | (null);
  existingPolicyId: (UUID) | (null);
  existingVersion: (string) | (null);
  versionChanged: boolean;
  submittedPolicyId: (UUID) | (null);
  submittedVersion: (string) | (null);
  createdAt: Timestamp;
};

export type CandidateList = {
  items: Array<CandidateSummary>;
  nextCursor: (string) | (null);
};

export type CandidateDetail = {
  id: UUID;
  runId: UUID;
  revision: UUID;
  status: CandidateStatus;
  sourceUrl: OfficialUrl;
  fetchedAt: Timestamp;
  contentHash: string;
  rawHash: (string) | (null);
  metadata: CandidateMetadata;
  attachmentUrls: Array<OfficialUrl>;
  unsupportedAttachmentUrls: Array<string>;
  missingFields: Array<string>;
  bodyText: string;
  criteria: Array<Criterion>;
  validationFindings: Array<ValidationFinding>;
  usage: (ModelUsage) | (null);
  suspectedDuplicateOf: (UUID) | (null);
  suspectedDuplicateSourceUrl: (OfficialUrl) | (null);
  duplicateOf: (UUID) | (null);
  existingPolicyId: (UUID) | (null);
  existingVersion: (string) | (null);
  versionChanged: boolean;
  submittedPolicyId: (UUID) | (null);
  submittedVersion: (string) | (null);
  discardReason: (string) | (null);
  createdAt: Timestamp;
  updatedAt: Timestamp;
};

export type CandidateUpdate = {
  metadata: CandidateMetadata;
  criteriaDrafts?: (Array<CriterionDraft>) | (null);
};

export type DiscardCandidateInput = {
  reason?: (string) | (null);
};
