# Entity relationship diagram

Phase 1 is implemented. Phase 2–6 entities are the approved target and will be
added as migrations land; they are shown here so naming and ownership can be
reviewed before code is written.

```mermaid
erDiagram
    User ||--o{ ConsentRecord : accepts
    User ||--o| PatientProfile : has
    User ||--o| PhysicianProfile : has
    User ||--o| StaffProfile : has

    PatientProfile ||--o{ Assessment : starts
    Assessment ||--o{ AssessmentStep : contains
    Assessment ||--o{ LabUpload : attaches
    Assessment ||--o| Evaluation : produces
    Evaluation ||--o{ EvaluationStatusHistory : records
    LabUpload }o--|| User : uploaded_by
    LabUpload }o--o| User : reviewed_by

    Evaluation }o--|| PhysicianProfile : reviewer
    PatientProfile ||--o{ Consultation : attends
    PhysicianProfile ||--o{ Consultation : conducts
    PhysicianProfile ||--o{ PhysicianAvailability : publishes

    Evaluation ||--o{ PrescribedTreatment : authorises
    Program ||--o{ PrescribedTreatment : protocol
    User ||--o{ PrescribedTreatment : patient
    User ||--o{ PrescribedTreatment : prescriber

    Program ||--o{ ContentItem : describes
    Order ||--o{ OrderItem : contains
    Order }o--|| User : patient
    OrderItem }o--|| Program : programme
    Subscription }o--|| User : patient
    Subscription ||--o{ Order : renews
    Payment }o--|| Order : pays
    Payment }o--o| User : refunded_by

    MessageThread ||--o{ Message : contains
    Message ||--o{ MessageAttachment : attaches
    MessageThread }o--|| User : patient
    MessageThread }o--o| User : physician
    MessageThread }o--o| User : nurse

    SupportTicket }o--|| User : opened_by
    ContactMessage
    ComplaintBookEntry
    AuditLog
```

## Notes

- `User` is the single identity for all roles; profiles are one-to-one.
- `Assessment` is versioned and resumable. Steps have typed payloads validated
  per step. Measurements are stored in kg/cm only.
- `Evaluation` is the state machine:
  `SUBMITTED → IN_REVIEW → APPROVED | NOT_SUITABLE | PENDING_LABS`.
  Every transition appends `EvaluationStatusHistory`; the history table has no
  update/delete API.
- `LabUpload` files are private. A file is physician-visible only when its scan
  status is `CLEAN`; downloads are short-lived pre-signed URLs.
- `ComplaintBookEntry` has an immutable, gap-free sequential number required by
  Peruvian consumer-protection rules. Legal wording is marked `LEGAL_REVIEW`.
- `AuditLog` is append-only and records actor, action, object, request id, IP,
  user agent and a before/after diff with PHI redaction at the application edge.

The Phase 1 ERD (users and consents only) is also in
[architecture.md](architecture.md#phase-1-erd).
