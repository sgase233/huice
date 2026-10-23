# Purpose: verify the multiagent handoff, stopping conditions, and rule-result integrity.
# Call: run with pytest after M1 implements AgentContext, agents, orchestrator, and fixtures.
# Output constraint: fail on uncited publication, missing-fact satisfied result, result overwrite, or hidden degradation.
