# Purpose: read/write Redis parsed-policy and assessment caches.
# Call: call from orchestrator/versioning with versioned keys.
# Output constraint: keys include enterprise/policy/policyVersion/factVersion/ruleVersion/period/asOf; recheck validity on hits; never reuse runId or replace persisted evidence.
