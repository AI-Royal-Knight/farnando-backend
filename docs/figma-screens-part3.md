# Figma review record — API Contract Part 3 (v1.0)

Source: contract Part 3 section 11, based on the patient-portal frames of Figma
file `ck6is4EPRiiQKNclaaFCFG` (layer names and structure; the live tools were
rate-limited, so nothing was edited in Figma). None of these items changes the
API surface. They are tracked in `docs/open-items.md`.

Status: **not fixed in Figma**.

## 11.2 Errors to fix

| # | Where | Defect | Backend truth | Required design fix |
| --- | --- | --- | --- | --- |
| 1 | Orders, physician calendar | Old brand: "#VIT-ORD-9912", "Vitalis Brand Logo". | Order numbers are `ADL-ORD-` plus a sequence (`ORDER_NUMBER_PREFIX`). | Use the `number` field and the Adelgaceya logo. |
| 2 | Orders table | Rows not sorted; one row has no date. | `GET /orders/` sorts by `-placed_at`; `placed_at` is always set. | Sort newest first and show `placed_at` on every row. |
| 3 | Overview, orders, video, calendar | Dates from different months and years. | All instants are UTC with `Z`; schedules carry `timezone`. | Use one consistent demo timeline. |
| 4 | Consultation card | "Not confirm yet". | `status: REQUESTED`. | "Not confirmed yet". |
| 5 | Patient frame | Physician actions ("Set Availability", "Accept consultation") on a patient screen. | Patients can join, message, reschedule and cancel only. | Remove physician actions from patient screens. |
| 6 | Select Physician | Search by "NPI" (US). | `license_label` holds the CMP number. | Use "CMP". |
| 7 | Treatments | "Semaglutide GLP-1 (0.5 mg SubQ)" vs "Semaglutide 0.5mg". | List: `medication_name` + `strength_label`; detail hero: `title`. | Use one naming pattern. |
| 8 | Sidebar | Different links per screen. | `GET /patients/me/overview/` returns `sections` with `enabled`. | Drive the sidebar from `sections`. |
| 9 | Treatment cards | Refills on one card only. | `refills` is `null` when the product has no refills. | Same columns on every card; show a dash for `null`. |
| 10 | Messages | Hotlinked Google avatars; "Dr. Marcus" vs "Dr. Marcus Vance". | `avatar_url` and `name` come from the clinician profile. | Host avatars; use the API name. |
| 11 | Physician calendar | Google Calendar clone elements. | Part 4. | Remove non-applicable elements. |
| 12 | Product images | Readable vial labels. | `Product.image_url` is admin content. | Use mockups. |

## 11.3 Missing designs

The backend already supports every flow below; each needs screens.

| Flow | Endpoints |
| --- | --- |
| Booking (slot picker, confirmation, "awaiting physician", reschedule and cancel dialogs) | `GET /physicians/{id}/availability/`, `POST /consultations/`, `.../reschedule/`, `.../cancel/` |
| Video (waiting room, device check, call ended, connection error) | `POST /consultations/{id}/join/`, `.../leave/` |
| Checkout (package, address, Culqi, confirmation, billing document) | `GET /programs/{id}/packages/`, `POST /orders/quote/`, `POST /orders/`, `GET /payments/{id}/` |
| Addresses (list, add/edit with department, province, district) | `/addresses/*`, `GET /geo/ubigeo/` |
| Subscriptions (list, pause, resume, cancel, past-due banner) | `/subscriptions/*` |
| Payment methods | `/payment-methods/*` |
| Profile and settings | `GET/PATCH /patients/me/` |
| Notifications center and bell | `/notifications/*` |
| Renewal request and "refills low" | `POST /treatments/{id}/renewal-requests/`, `REFILL_LOW` |
| Empty states | every list endpoint returns an empty page |
| Order detail (timeline, tracking) | `GET /orders/{id}/` |
