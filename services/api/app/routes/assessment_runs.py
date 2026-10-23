# Purpose: expose run creation/read, report.md, and trace endpoints.
# Call: API-11..14; pass AssessmentInput separately from AgentContext; report returns Markdown on success and JSON errors on failure.
# Output constraint: immutable snapshot/new runId on recompute; resolve per-policy validity at read time; trace requires admin Bearer.
