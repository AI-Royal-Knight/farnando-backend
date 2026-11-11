# Figma review record — API Contract Part 4 (v1.0)

Source: contract Part 4 section 10 (clinician frames of Figma file
`ck6is4EPRiiQKNclaaFCFG`, from layer names; the live tools were rate-limited).
Nothing was edited in Figma. None of these items changes the API.

Status: **not fixed in Figma**.

## 10.2 Errors to fix

| # | Where | Defect | Backend truth | Required design fix |
| --- | --- | --- | --- | --- |
| 1 | Create Physician Account | US phone placeholder. | `phone` is E.164; Peru is `+51`. | Use a `+51` placeholder. |
| 2 | Physician sidebar | Shows the patient menu and "Medical Assessment". | Clinician areas: dashboard, queue, patients, consultations, calendar, messages. | Overview, Pending Reviews, My Patients, Consultations, Calendar, Messages. |
| 3 | Physician consultations | Built on the patient layout. | `GET /clinical/consultations/` returns `ClinicianConsultationItem`. | Rebuild as a physician list. |
| 4 | Physician messages | Shows the patient's threads. | Clinicians get patient participants and `patient_context`. | Design the clinician thread view. |
| 5 | Calendar | "Vitalis Brand Logo". | — | Use the Adelgaceya logo. |
| 6 | Calendar | Google Calendar copy, 24 h day from 1 AM. | `GET /physicians/me/calendar/` returns slots, consultations and blocks. | Simple day/week view in working hours. |
| 7 | Account form | "Registration Number \| MEDICAL LICENSE", "Specialty Reg Number" default "1", no confirm password. | `license_number`, `specialty_registration_number` (only when the specialty needs it), `confirm_password`. | Fix labels and add the field. |
| 8 | Review modal | No request-labs, prescription fields or flag acknowledgement. | `request-labs`, `approve.prescription`, `acknowledged_flag_option_ids`, `attestation`. | Add them. |

## 10.3 Missing designs

| Flow | Endpoints |
| --- | --- |
| MFA login, enrolment (QR, backup codes) | `/auth/login/`, `/auth/mfa/*` |
| Invitation expired/used, "credentials being verified" | `GET /public/physician-invitations/{token}/`, `verification_status` |
| Prescription form, reject, request-labs, flags, attestation | `/clinical/evaluations/{id}/approve|reject|request-labs/`, `/clinical/products/` |
| Patient chart, notes, dose adjustment, pause/discontinue, renewal queue | `/clinical/patients/*`, `/clinical/treatments/*`, `/clinical/renewal-requests/*` |
| Complete-visit form, decline/cancel dialogs, physician video view | `/clinical/consultations/*` |
| Availability editor, profile/settings, notifications, empty states | `/physicians/me/*`, `/notifications/*` |
