// Purpose: centralize HTTP requests for /api/v1.
// Call: page functions per the root implementation guide; encode path/query; keep target version in JSON and ETag/Idempotency-Key in headers.
// Output constraint: JSON vs Markdown success/error parsing; expose ETag/X-Trace-Id; include credentials, runtime-only admin token; never auto-retry writes or remap enums.
