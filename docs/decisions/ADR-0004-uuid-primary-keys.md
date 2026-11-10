# ADR-0004: UUIDv4 primary keys

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

Public APIs expose record identifiers. Sequential integers enable enumeration
and leak volume. Django 5.2 has no native UUIDv7 field; UUIDv7 libraries add a
dependency and database-generated values complicate test databases.

## Decision

Use UUIDv4 (`default=uuid.uuid4`) for all domain primary keys via `BaseModel`,
and for `User`. Human-facing sequential numbers are added only where the law
requires them (e.g. complaints book) and are separate from the PK.

## Consequences

- Non-enumerable URLs and safer multi-tenant joins.
- Random UUIDs have worse B-tree locality than sequential integers; acceptable
  at this scale, and indexes remain explicit.
- Clients must treat identifiers as opaque strings, never parse them.
- If write volume grows, a UUIDv7 migration can be evaluated with an ADR.
