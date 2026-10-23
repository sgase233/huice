# Purpose: access RDS PostgreSQL records and policy versions.
# Call: call from routes/agents through repository functions.
# Output constraint: persist immutable runs and versioned policies; no SQL in route handlers.
