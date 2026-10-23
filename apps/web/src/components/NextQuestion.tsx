// Purpose: render one prioritized missing-fact question.
// Call: props {question,onAnswer(value),onSkip(),pending}; parent PATCHes question.factRef.key+period, then creates a new run with supersedesRunId.
// Output constraint: rank comes from backend; skip stays local without PATCH; null remains unknown, never not_satisfied.
