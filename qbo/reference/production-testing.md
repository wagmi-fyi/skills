# Production Realm Testing

How to safely verify QBO behavior against a live production realm when no sandbox
keys exist. Live probes are acceptable ONLY with explicit user approval and ALL of
the rails below — they create real objects in real books.

## Rails

- **Existing contacts only.** Customers/Vendors cannot be deleted — only made
  inactive. Never create test contacts; reuse a real one.
- **Transactions are hard-deletable.** Invoice, Payment, JournalEntry, etc. support
  `.delete(qb=client)`. GET the object fresh first — delete requires the current
  SyncToken.
- **Minimal amounts** (e.g. $0.01) on **current-period dates** — never closed,
  reconciled, or published periods.
- **Tag every created object** with a searchable token in PrivateNote (e.g.
  `[test:<probe>:<n>]`) so cleanup is verifiable by query, not by memory.
- **Clean up in a `finally` block** within the same session — a probe that errors
  mid-way must still attempt deletion of everything it created, and print manual
  cleanup instructions for anything it could not delete.
- **Verify clean** after cleanup: query each tag and confirm zero results. Deleted
  transactions drop out of queries; QBO's audit log retains a record (expected,
  harmless).
- **Report created/deleted ids** in the probe output so a human can spot-check.

## Probe techniques

- **Force a real 401** (token-refresh testing): corrupt
  `client.session.access_token` — NOT `auth_client.access_token`. The SDK's
  `_start_session` builds an OAuth2Session with a **copy** of the token at
  construction; requests read the session's copy, so corrupting the auth_client is
  a no-op.
- **Duplicate-DocNumber enforcement is a per-realm setting** — probe it before
  relying on it: create a document, then attempt a second create with the same
  DocNumber. Enforcing realms raise fault **6140** ("Duplicate Document Number
  Error") and the second object does NOT post; non-enforcing realms accept both
  (delete both).
- **Read-after-write lag:** a just-created object is usually queryable in under a
  second, but treat an immediate empty query result as inconclusive rather than
  proof of absence — re-query after a short delay before concluding.
