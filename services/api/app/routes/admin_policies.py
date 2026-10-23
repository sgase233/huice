# Purpose: implement API-04..07 and API-15/16: import/extract/save/publish plus protected draft list/detail.
# Call: admin Bearer router; writes select body.version and compare If-Match to draft.revision; extract also takes Idempotency-Key.
# Output constraint: return ETag/findings per OpenAPI; save failed citation drafts but block publication; public routes never expose drafts.
