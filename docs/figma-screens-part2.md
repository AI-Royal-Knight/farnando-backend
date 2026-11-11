# Figma review record — API Contract Part 2 (v1.0)

Source: the Part 2 design frames that accompany contract v1.0 (assessment
flow). This file records design/copy defects found in Part 2 section 9 and how
they relate to the implemented backend. None of these defects changes the API
surface; the backend implements the contract, not the mock-up copy.

Status: **not fixed in Figma**. The items below are frontend/design work and are
tracked in `docs/open-items.md`. They must not be "fixed" by changing API
fields, enums or clinical rules.

## 9.1 Overview and Body Measurements

| # | Where | Defect | Backend truth | Required design fix |
| --- | --- | --- | --- | --- |
| 1 | Patient Overview stepper | The stepper skips step 4 (shows 3 → 5). | The journey has five ordered steps: `FIRST_ASSESSMENT`, `SECOND_ASSESSMENT`, `ELIGIBILITY_REVIEW`, `PRESCRIPTION`, `ORDER` (contract journey). | Show all five steps with stable numbering. |
| 2 | Overview projection card | Shows `200 lbs (82 kg)` → `158 lbs (70 kg)` and `-42 lbs (-12 kg)`. 82 kg is about 181 lb and 70 kg is about 154 lb, so the loss is about 26–27 lb, not 42 lb. | The server returns canonical kg plus a `display` value in the assessment's unit. Projection points are computed in kg and clamped at the target. | Use the API's `display` values; never hardcode a second conversion in the UI. |
| 3 | Body Measurements, "Date of birth" card | Contains Male/Female/Other buttons instead of a date input. | The request field is `date_of_birth: YYYY-MM-DD` (a date, not a sex value). `sex` is a separate enum field with `MALE`, `FEMALE`, `OTHER`. | Replace the buttons with a date input (with a valid past-date state). Keep sex as a separate control. |
| 4 | Body Measurements, units | The screen mixes lb and kg in the same projection copy. | Requests accept `KG`/`LB` and `CM`/`IN`; the response always has `kg`/`cm` and `display`. The assessment remembers the units of the last save until Part 3 profiles add a preference. | Pick one unit per field and format from the response; do not mix units in one sentence. |

## 9.2 Questionnaire and health conditions

| # | Where | Defect | Backend truth | Required design fix |
| --- | --- | --- | --- | --- |
| 5 | Health Conditions step | The medication question appears twice (for example a condition list and a separate medication list). | The seeded questionnaire has one `takes_medications` (`YES_NO`) question and one conditional `medication_list` text question, shown only when the answer is `YES`. | Remove the duplicate question; keep the conditional text field. |
| 6 | BMI scale | The scale labels the 18.5–24.9 band "Normal" while the API uses `HEALTHY`. | `BmiCategory.HEALTHY` with label "Healthy" in the `bmi_scale` response. | Use the label from the API (`Healthy`) so the UI and enum agree. |
| 7 | Screening options | Some screening choices appear twice (for example a condition and its "other" variant). | Options are admin-configured; each option has an id and an admin-only screening effect. Duplicate labels are a content defect, not an API constraint. | De-duplicate the option list in the questionnaire configuration. |
| 8 | Conditional fields | The medication text field is visible when the answer is `NO`. | `show_if` returns `{question_id, equals}`; hidden answers are discarded by the server. | Hide/clear the text field when `takes_medications` is not `YES`. |
| 9 | Summary copy | The summary mixes "% complete" and step completion. | `assessment_incomplete` returns `details.missing_steps`; the progress helper counts visible required questions. | Drive the progress UI from `missing_steps`, not a local guess. |

## 9.3 Fee, labs and evaluation

| # | Where | Defect | Backend truth | Required design fix |
| --- | --- | --- | --- | --- |
| 10 | Fee screen | Shows `$49.00` while the patient flow and seeded configuration use `$25.00`. | `EvaluationFee.amount` is a snapshot of `Program.fee_amount` (`25.00 USD` by default). The mismatch is `[OPEN]` in `docs/open-items.md`. | Confirm the commercial amount, then set it in the admin/program config; do not hardcode it in the UI. |
| 11 | Payment copy | The provider is spelled inconsistently ("Culqi"/"Culqui"). | The only method code is `CULQI` with label `Culqi`. | Use "Culqi" consistently. |
| 12 | Labs upload | The example lab filename is unclear/truncated. | Uploads accept `file_name` of 1–150 characters, PDF/PNG/JPEG, 10 MB max, then a pre-signed `PUT` and a confirm step. | Show a clear example filename and the accepted types/size, plus the scanning state. |
| 13 | Lab list | Rejected uploads are shown as if they were submitted. | `LabUpload.status` is `PENDING_UPLOAD`, `SCANNING`, `CLEAN` or `REJECTED`; `rejection_reason` is one of `VIRUS_DETECTED`, `INVALID_FILE_TYPE`, `FILE_TOO_LARGE`, `UPLOAD_MISSING`, `UNREADABLE`. | Show a rejected state with the reason and a retry action; only `CLEAN` files can be downloaded. |
| 14 | Evaluation checklist | The checklist implies labs are mandatory before payment/review. | Lab submission is optional: `labs/skip` sets `DEFERRED`; a physician can request labs later (`PENDING_LABS`). | Show `DEFERRED` as an intentional state, not an error. |
| 15 | Prescription | A download button is shown before approval or before a PDF exists. | Download requires `APPROVED`, a stored `Prescription.pdf_key` and `can_download`; otherwise the API returns `409 invalid_state_transition`. | Disable the button until that state, and handle 409 with a clear message. |

## 9.4 Admin and missing states

| # | Where | Defect | Backend truth | Required design fix |
| --- | --- | --- | --- | --- |
| 16 | Admin question editor | Wording implies options are free text or that screening effects are patient-visible. | Effects are admin-only; the patient questionnaire never serializes `screening_effect`. | Label the effect field "internal clinical rule", and keep it out of patient previews. |
| 17 | Admin question editor | No guidance on `show_if` / `show_if_equals`. | `show_if` returns `{question_id, equals}`; the condition must reference an earlier question. | Document the conditional-field control and validate the reference in the UI. |
| 18 | Fee/payment error | No designed state for a declined card. | `402 payment_failed` with a localized message; the payment row is `FAILED` and a new token is required. | Add a decline state with "try again" and no assumption that the same token can be reused. |
| 19 | Lab upload error | No designed state for a missing upload, invalid type or oversized file. | `400 validation_error` with `rejection_reason` `UPLOAD_MISSING`/`INVALID_FILE_TYPE`/`FILE_TOO_LARGE`. | Show the reason and the upload limits. |
| 20 | Assessment locked | No designed state after submit or cancellation. | Writes after submit return `409 assessment_locked`; cancellation from a non-cancellable state returns `409 invalid_state_transition`. | Add read-only/locked states with the next action. |
| 21 | Screening out | No designed state for `SCREENED_OUT` and the review request. | Submit returns `SCREENED_OUT_MESSAGE`; `request-review` is allowed once and keeps `evaluation_status: SCREENED_OUT`. | Design the decline explanation, the one-time review request and its confirmation. |
| 22 | Empty/loading states | Not designed for an empty program list, no assessments, or no lab files. | Programs are active-only; assessments are paginated; labs submit without files returns `409 no_lab_files`. | Add empty and loading states; never infer data the API did not return. |

## Backend surface used by these screens

| Area | Endpoint |
| --- | --- |
| Programs | `GET /api/v1/programs/` |
| Assessments | `GET/POST /api/v1/assessments/`, `GET /api/v1/assessments/{id}/` |
| Questionnaire | `GET /api/v1/assessments/{id}/questionnaire/` |
| Measurements | `PUT /api/v1/assessments/{id}/measurements/` |
| Steps | `PUT /api/v1/assessments/{id}/steps/{step_key}/` |
| Summary | `GET /api/v1/assessments/{id}/summary/` |
| Submit / review / cancel | `POST .../submit/`, `.../request-review/`, `.../cancel/` |
| Fee / payment | `GET .../fee/`, `POST .../fee/pay/` |
| Labs | `GET .../lab-recommendations/`, `GET/POST /api/v1/lab-uploads/`, `.../confirm/`, `.../download/`, `POST .../labs/submit/`, `.../labs/skip/` |
| Evaluation / prescription | `GET .../evaluation/`, `GET .../evaluation/prescription/download/` |

## Content decisions (not changed in Figma)

1. The `$49.00` admin example and the `$25.00` patient flow must be reconciled by
   the business; the backend treats the program fee as configuration.
2. The projection is illustrative. Its weekly-loss assumption, months and
   disclaimer are editable configuration and need clinical/legal sign-off.
3. The seeded questionnaire, screening effects and BMI/age thresholds are
   proposals for `[MEDICAL_REVIEW]`; the admin is the source of truth.
4. Lab retention and deletion periods need `[LEGAL_REVIEW]`.
5. Culqi is the only payment method exposed; the real provider adapter and
   webhook are Part 3.
