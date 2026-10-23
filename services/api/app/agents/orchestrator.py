# Purpose: coordinate the online profile, published-rule evaluation, explanation, and trace pipeline.
# Call: assess(input: AssessmentInput, context: AgentContext); enterpriseId/policyIds belong to input, not undeclared context fields.
# Output constraint: pin snapshots; call rules then questioning then explanation; persist new runId/dependencies; link offline trace; never overwrite rule result.
