# Purpose: define shared internal AgentContext, AgentResult, EvidenceRef, and status contracts.
# Call: import from orchestrator and the four role agents; map to app/schemas.py at API boundaries.
# Output constraint: context includes period/as_of/version map/budget; business IDs are AssessmentInput; retain multi-source evidence; no secrets or needs_review-as-success.
