# Purpose: rank missing facts for one next question.
# Call: choose_next_question(assessments, skipped_fact_refs) after rules; deduplicate by key+period.
# Output constraint: rank by affected-policy count, cost, sensitivity, then key/date; return FactRef/prompt/reason/rank or null; skip never updates facts.
