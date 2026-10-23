# Purpose: track policy/fact versions and assessment validity.
# Call: invalidate_dependents(change) in mutation transaction; resolve_validity(snapshot, events, now) on run/report/cache reads.
# Output constraint: only affected policy/FactRef dependencies become stale; preserve historical result/snapshots; validity and staleReasons are read-time views.
