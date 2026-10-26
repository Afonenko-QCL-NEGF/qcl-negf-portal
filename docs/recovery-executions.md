# Recovery executions

The separate read-only panel lists exact AiiDA execution restart WorkChains,
including standalone recoveries. It does not add them to Plan workflow `/runs`
or grant access to saved results, exports, cancellation or submission actions.

Bearer-protected `GET /api/v1/recovery-executions?limit=25&offset=0` returns
`recovery_executions`; limits are 1–100 with a nonnegative offset. Detail is
`GET /api/v1/recovery-executions/{uuid}`. AiiDA owns exact process type checks,
CALL_WORK caller identity, direct CALL_CALC children and metadata bounds.
An older API returning 404 leaves this panel unavailable and existing runs usable.
The browser uses independent selection, pagination and request generations;
pages are current offset views, not immutable snapshots under concurrent inserts.

Scheduler job ID/state is stored AiiDA metadata, not a live Slurm query.
Plan fingerprint is stored provenance. Recovery bundle/prior archive UUIDs are
input associations, not verified previous-run or scientific-result claims.
Missing optional metadata remains unknown. Scientific acceptance is not measured
by this view, independently of process completion or scheduler status.

The public packaged HTML entry paths `/` and `/index.html` use
`Cache-Control: no-cache, must-revalidate` and direct GET/HEAD FileResponse.
Conditional HTML requests return current bytes with 200 even when stat validators
collide across releases. Range retains standard FileResponse behavior.
Hashed assets keep StaticFiles caching; API remains no-store. Existing fresh
browser cache entries can bypass a request: a hard reload or one release-specific
query link is needed to enter the new policy. The observed stale UI is compatible
with cached HTML; the precise cause was not established by a wire cache trace.
