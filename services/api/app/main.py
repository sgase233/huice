# Purpose: assemble the FastAPI app and route modules.
# Call: uvicorn app.main:app from services/api after implementation.
# Output constraint: /api/v1; map errors to ApiError/X-Trace-Id; exact CORS with credentials and exposed ETag/trace/download headers; no business decisions.
