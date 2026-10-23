// Purpose: review extracted criteria and publish approved policy versions.
// Call: protected API-15 list -> API-16 detail -> API-04..07 writes; preserve latest ETag and target version; never use public detail for drafts.
// Output constraint: show failed findings, block invalid publish; keep edits on 409 and reload for manual merge; token only in memory.
