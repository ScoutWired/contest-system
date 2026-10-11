# Security notes
- Sessions: DB-backed random tokens, HttpOnly SameSite=Lax cookies (Secure in prod), expiry, logout revokes; guest revocation bumps epoch killing sessions promptly.
- CSRF: per-session token required on all POST forms; OAuth state validated and single-use.
- Guest codes: cryptographically random, stored as PBKDF2-SHA256 (210k rounds); shown once.
- Discord: minimum scope `identify`; roles fetched server-side via bot; fail closed; never trust browser-supplied ids.
- Votes: UNIQUE(poll_id, voter_id); transactions + IntegrityError race handling; close-then-count prevents late votes.
- Uploads: allowlist extensions, size cap, script/executable content rejected, stored outside web root, auth-gated.
- Rate limits: login IP+user throttling + per-account lockout, generic error messages.
- Audit: auth, moderation, polls, guests, roles, settings, deliveries.
- Votes by later-revoked accounts remain counted (documented policy) but revocation blocks all future votes.
