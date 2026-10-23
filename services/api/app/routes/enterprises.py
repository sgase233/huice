# Purpose: expose enterprise creation/read and fact update endpoints.
# Call: API-08/09/10; PATCH targets path.factKey + body.period and atomically compares If-Match to current factVersion.
# Output constraint: enforce FactKey/value/unit and unique key+period; server controls verified; return full Enterprise + ETag, null is unknown.
