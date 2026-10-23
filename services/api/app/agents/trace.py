# Purpose: collect ordered, redacted records from each agent, rule engine, and human review step.
# Call: orchestrator appends records; assessment_runs route maps them to API-14 TraceStep output.
# Output constraint: include stage/status/versions/elapsed time; omit keys, raw prompts, and sensitive facts.
