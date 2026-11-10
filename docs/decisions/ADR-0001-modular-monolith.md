# ADR-0001: Modular monolith with app-level service/selector layers

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

The product has distinct clinical, commercial and support workflows but a small
engineering team and one regulatory boundary. Microservices would multiply
deployment, authorization and audit surfaces.

## Decision

Build one Django project with domain apps under `apps/`. Enforce:

- `models.py` for persistence and constraints.
- `services.py` for writes, state machines and business rules.
- `selectors.py` for scoped read queries.
- thin `views.py` that validate, authorize and delegate.
- no business logic in serializers or views.

Apps may import `core` and their own modules; cross-app writes go through the
owning app's service to keep invariants enforced.

## Consequences

- Single transaction boundary for clinical + commercial consistency.
- Easy local development and one migration history.
- Requires discipline to prevent app coupling; CI/type checks and review enforce
  the layering.
- If a module later needs independent scaling, the service boundary is a clean
  extraction seam.
