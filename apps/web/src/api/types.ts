// Generated from contracts/openapi.yaml by contracts/generate_types.py; do not edit manually.
// Wire types only: semantic validation and HTTP calls remain to be implemented.

export type UUID = string;

export type Date = string;

export type Timestamp = string;

export type DecimalString = string;

export type OfficialUrl = string;

export type Period = {
  startDate: Date;
  endDate: Date;
};

export type FactKey = "region" | "industry" | "establishedDate" | "taxableIncome" | "rdExpense" | "rdActivity" | "employeeCountStart" | "employeeCountEnd" | "assetsStart" | "assetsEnd";

export type FactUnit = "code" | "date" | "CNY" | "boolean" | "person" | "ratio";

export type AssessmentResult = "satisfied" | "not_satisfied" | "insufficient_evidence" | "conflict";

export type Validity = "current" | "stale";

export type PolicyStatus = "published" | "closed";

export type DraftStatus = "pending_parse" | "pending_review" | "published" | "closed";

export type PolicyType = "tax" | "grant";

export type AgentStatus = "ok" | "needs_review" | "retryable_error" | "fatal_error";

export type FactSource = "user_input" | "synthetic_material";

export type Comparator = "lt" | "lte" | "eq" | "gte" | "gt" | "ne";

export type FactRef = {
  key: FactKey;
  period: Period;
};

export type FactInput = {
  key: FactKey;
  value: (string) | (boolean) | (null);
  unit: FactUnit;
  period: Period;
  source: FactSource;
};

export type EnterpriseFact = {
  key: FactKey;
  value: (string) | (boolean) | (null);
  unit: FactUnit;
  period: Period;
  source: FactSource;
  verified: boolean;
};

export type FactUpdate = {
  value: (string) | (boolean) | (null);
  unit: FactUnit;
  period: Period;
  source: FactSource;
};

export type EnterpriseInput = {
  nameSynthetic: string;
  period: Period;
  facts: Array<FactInput>;
};

export type Enterprise = {
  id: UUID;
  nameSynthetic: string;
  period: Period;
  facts: Array<EnterpriseFact>;
  factVersion: string;
};

export type ApplicationWindow = {
  opensAt: Timestamp;
  closesAt: Timestamp;
};

export type SourceSpan = {
  sourceId: UUID;
  sourceUrl: OfficialUrl;
  sourceVersion: string;
  page: (number) | (null);
  section: (string) | (null);
  quote: string;
  textHash: string;
};

export type EvidenceRef = {
  policyId: UUID;
  sourceId: UUID;
  sourceUrl: OfficialUrl;
  sourceVersion: string;
  page: (number) | (null);
  section: (string) | (null);
  quote: string;
  textHash: string;
};

export type SourceDocument = {
  id: UUID;
  kind: "body" | "attachment";
  sourceUrl: OfficialUrl;
  title: string;
  fetchedAt: Timestamp;
  contentHash: string;
  text: string;
};

export type ExpressionPeriod = ("assessment") | (Period);

export type Expression = (AndExpression) | (OrExpression) | (NotExpression) | (CompareExpression) | (DateExpression) | (IndustryExclusionExpression) | (QuarterlyAverageExpression);

export type AndExpression = {
  op: "and";
  children: Array<Expression>;
};

export type OrExpression = {
  op: "or";
  children: Array<Expression>;
};

export type NotExpression = {
  op: "not";
  child: Expression;
};

export type CompareExpression = {
  op: "compare";
  factKey: FactKey;
  period: ExpressionPeriod;
  comparator: Comparator;
  value: (string) | (boolean);
  unit: FactUnit;
};

export type DateExpression = {
  op: "date";
  factKey: "establishedDate";
  period: ExpressionPeriod;
  comparator: Comparator;
  value: Date;
};

export type IndustryExclusionExpression = {
  op: "industry_exclusion";
  factKey: "industry";
  period: ExpressionPeriod;
  excluded: Array<string>;
};

export type QuarterlyAverageExpression = {
  op: "quarterly_average";
  metric: "employeeCount" | "assets";
  period: ExpressionPeriod;
  comparator: Comparator;
  value: DecimalString;
  unit: "person" | "CNY";
};

export type CriterionDraft = {
  id: UUID;
  title: string;
  expression: Expression;
  sourceSpans: Array<SourceSpan>;
  reviewFinding: "none" | "conflict";
};

export type Criterion = {
  id: UUID;
  title: string;
  expression: Expression;
  sourceSpans: Array<SourceSpan>;
  reviewFinding: "none" | "conflict";
  validationStatus: "pending" | "passed" | "failed";
};

export type ValidationFinding = {
  criterionId: (UUID) | (null);
  code: string;
  message: string;
  sourceSpans: Array<SourceSpan>;
};

export type PreparationTemplateItem = {
  title: string;
  description: string;
  requiredFactRefs: Array<FactRef>;
};

export type PreparationItem = {
  title: string;
  description: string;
  requiredFactRefs: Array<FactRef>;
  missing: boolean;
};

export type PreparationTemplate = {
  type: PolicyType;
  handlingMode: "tax_self_assessment" | "grant_application";
  officialEntryUrl: OfficialUrl;
  deadline: (Timestamp) | (null);
  items: Array<PreparationTemplateItem>;
};

export type Preparation = {
  type: PolicyType;
  handlingMode: "tax_self_assessment" | "grant_application";
  officialEntryUrl: OfficialUrl;
  deadline: (Timestamp) | (null);
  items: Array<PreparationItem>;
};

export type PolicyImportMetadata = {
  policyId?: UUID;
  version: string;
  title: string;
  type: PolicyType;
  issuer: string;
  region: string;
  documentNumber: (string) | (null);
  applicableEntities: Array<string>;
  publishedAt: Date;
  effectiveFrom: Date;
  effectiveTo: (Date) | (null);
  applicationWindow: (ApplicationWindow) | (null);
  sourceUrl: OfficialUrl;
  officialEntryUrl: OfficialUrl;
};

export type PolicySummary = {
  id: UUID;
  title: string;
  type: PolicyType;
  issuer: string;
  region: string;
  version: string;
  status: PolicyStatus;
  effectiveFrom: Date;
  effectiveTo: (Date) | (null);
  applicationWindow: (ApplicationWindow) | (null);
  sourceUrl: OfficialUrl;
};

export type PolicyDetail = {
  id: UUID;
  title: string;
  type: PolicyType;
  issuer: string;
  region: string;
  version: string;
  status: PolicyStatus;
  effectiveFrom: Date;
  effectiveTo: (Date) | (null);
  applicationWindow: (ApplicationWindow) | (null);
  sourceUrl: OfficialUrl;
  documentNumber: (string) | (null);
  applicableEntities: Array<string>;
  publishedAt: Date;
  officialEntryUrl: OfficialUrl;
  sources: Array<SourceDocument>;
  criteria: Array<PublishedCriterion>;
  preparationTemplate: PreparationTemplate;
};

export type PolicyDraft = {
  id: UUID;
  version: string;
  title: string;
  type: PolicyType;
  issuer: string;
  region: string;
  documentNumber: (string) | (null);
  applicableEntities: Array<string>;
  publishedAt: Date;
  effectiveFrom: Date;
  effectiveTo: (Date) | (null);
  applicationWindow: (ApplicationWindow) | (null);
  sourceUrl: OfficialUrl;
  officialEntryUrl: OfficialUrl;
  status: DraftStatus;
  revision: UUID;
  sources: Array<SourceDocument>;
  criteria: Array<Criterion>;
  validationFindings: Array<ValidationFinding>;
};

export type PolicyList = {
  items: Array<PolicySummary>;
  nextCursor: (string) | (null);
};

export type PolicyDraftList = {
  items: Array<PolicyDraft>;
  nextCursor: (string) | (null);
};

export type ModelUsage = {
  mode: "deepseek" | "fixture" | "saved";
  modelVersion: (string) | (null);
  inputTokens: number;
  outputTokens: number;
  elapsedMs: number;
};

export type ExtractPolicyInput = {
  version: string;
  modelVersion?: string;
};

export type ExtractionResult = {
  draft: PolicyDraft;
  criteriaDrafts: Array<Criterion>;
  validationFindings: Array<ValidationFinding>;
  usage: ModelUsage;
};

export type SaveCriteriaInput = {
  version: string;
  criteria: Array<CriterionDraft>;
};

export type SaveCriteriaResult = {
  draft: PolicyDraft;
  criteria: Array<Criterion>;
  validationFindings: Array<ValidationFinding>;
};

export type PublishPolicyInput = {
  version: string;
  reviewConfirmed: true;
};

export type NextQuestion = {
  factRef: FactRef;
  prompt: string;
  reason: string;
  rank: number;
};

export type StaleReason = {
  code: "policy_changed" | "fact_changed" | "window_closed" | "policy_expired" | "valid_until_passed";
  message: string;
  changedCriterionIds: Array<UUID>;
  changedFactRefs: Array<FactRef>;
};

export type CalculationInput = {
  factRef: FactRef;
  value: (DecimalString) | (null);
};

export type Calculation = {
  formula: string;
  inputs: Array<CalculationInput>;
  intermediateValues: Array<DecimalString>;
  value: (DecimalString) | (null);
  unit: FactUnit;
};

export type CriterionResult = {
  criterionId: UUID;
  result: AssessmentResult;
  reason: string;
  factRefs: Array<FactRef>;
  evidenceRefs: Array<EvidenceRef>;
  calculation: (Calculation) | (null);
};

export type PolicyAssessment = {
  policyId: UUID;
  result: AssessmentResult;
  validity: Validity;
  staleReasons: Array<StaleReason>;
  criteriaResults: Array<CriterionResult>;
  missingFacts: Array<FactRef>;
  nextQuestion: (NextQuestion) | (null);
  validUntil: (Timestamp) | (null);
  preparation: Preparation;
  explanation: string;
  explanationMode: "deepseek" | "saved" | "fixture";
  warnings: Array<string>;
};

export type AssessmentInput = {
  enterpriseId: UUID;
  policyIds: Array<UUID>;
  asOf: Timestamp;
  period: Period;
  supersedesRunId?: UUID;
  skippedFactRefs?: Array<FactRef>;
};

export type AssessmentRun = {
  id: UUID;
  enterpriseId: UUID;
  period: Period;
  asOf: Timestamp;
  createdAt: Timestamp;
  policyVersionMap: {
  [key: string]: string;
};
  factVersion: string;
  ruleVersion: string;
  supersedesRunId: (UUID) | (null);
  assessments: Array<PolicyAssessment>;
};

export type TraceStep = {
  sequence: number;
  stage: "ingestion" | "extraction" | "citation_validation" | "human_review" | "profile" | "retrieval" | "evaluation" | "questioning" | "explanation";
  status: AgentStatus;
  inputSummary: string;
  outputSummary: string;
  evidenceRefs: Array<EvidenceRef>;
  policyId: (UUID) | (null);
  policyVersion: (string) | (null);
  modelOrRuleVersion: string;
  elapsedMs: number;
  errorCode: (string) | (null);
  humanAction: (string) | (null);
};

export type TraceResponse = {
  steps: Array<TraceStep>;
};

export type ApiError = {
  error: {
  code: string;
  message: string;
  details: {
  [key: string]: unknown;
};
};
  traceId: UUID;
};

export type HealthResponse = {
  status: "ok";
  version: string;
};

export type PublishedCriterion = {
  id: UUID;
  title: string;
  expression: Expression;
  sourceSpans: Array<SourceSpan>;
  reviewFinding: "none" | "conflict";
  validationStatus: "passed";
};
