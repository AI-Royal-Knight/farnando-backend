# Figma review record — API Contract Part 5 (v1.0)

Source: contract Part 5 section 13 (admin and staff frames of Figma file
`ck6is4EPRiiQKNclaaFCFG`, from layer names; the live tools were rate-limited).
Nothing was edited in Figma. None of these items changes the API
(deviation 134).

Status: **not fixed in Figma**.

## 13.2 Errors to fix

| # | Where | Defect | Backend truth | Required design fix |
| --- | --- | --- | --- | --- |
| 1 | Admin/staff sidebars | Menus differ between screens; one still says "Link - Medical Assessment". | `GET /admin/dashboard/`, `GET /staff/dashboard/`; staff areas depend on `permissions`. | One sidebar per role; hide staff items the member lacks permission for. |
| 2 | Add Staff | Admin types the new member's password. | `POST /admin/staff/` creates an `INVITED` account and emails a single-use link. | Replace with an invitation form (no password). |
| 3 | Add Content | Image URL text fields. | `POST /admin/asset-uploads/` → PUT → `confirm/`; items take `*_upload_id`. | Image upload controls. |
| 4 | Question editor | "Option A (Correct / Selected)". | Options carry `screening_effect` (`NONE`, `NEEDS_REVIEW`, `NOT_ELIGIBLE`). | Screening-effect selector. |
| 5 | Evaluation fee | $49.00 (admin) vs $25 (patient). | `GET/PUT /admin/programs/{id}/evaluation-fee/` is the single source. | Show the API value in both places. |
| 6 | Order IDs | `#ORD-94821`, `#VIT-ORD-9912`. | `ADL-ORD-NNNNN`. | Use the real format. |
| 7 | Patient detail | Miami address, "FedEx Cold Chain". | Peruvian addresses (`ubigeo`), configured carriers. | Peruvian sample data. |
| 8 | Package prices | "$180"/month next to "$1,074" bi-annually. | `display_price_per_month` and `charge_per_cycle` are both stored; `price_mismatch` guards them. | Same rounding in both places. |
| 9 | Reports | Numbers and donut legend do not reconcile. | `GET /admin/reports/overview/` returns `definitions` and largest-remainder percentages. | Bind to the API values. |
| 10 | Support tickets table | Lowercase titles; missing requester, category, priority, assignee, age; mixes contact-form questions. | `GET /staff/tickets/` returns all of these; contact-form tickets carry `source`. | Add the columns and a source filter. |
| 11 | Staff Messages | Copy of the patient chat with a Resolve button. | Staff work from the ticket inbox. | Ticket inbox with requester, category and status. |
| 12 | Orders/Subscriptions | Orders table named "Subscriptions Table Card"; "New Refill" tab. | Separate `/staff/orders/` and `/staff/subscriptions/`. | Rename components; drop or define the tab. |
| 13 | Physician cards | Unlabelled "Stats Row (Requested)". | `GET /admin/physicians/` stats: assigned, active patients, consultations. | Label the stats. |
| 14 | Patient table actions | Assign, re-assign and view mixed in one column. | `POST /staff/patients/{id}/assign-physician/`. | One primary action plus an overflow menu. |

## 13.3 Missing designs

| Flow | Endpoints |
| --- | --- |
| Admin Overview; Staff Overview check | `/admin/dashboard/`, `/staff/dashboard/` |
| Staff/physician invitations, credential verification, suspend with reassignment, permission editor | `/admin/staff/*`, `/admin/physicians/*`, `/public/staff-invitations/*` |
| Ticket detail (internal notes, assign), complaints queue and respond form, privacy requests queue | `/staff/tickets/*`, `/staff/complaints/*`, `/admin/privacy-requests/*` |
| Evaluation fee, product/formulary, program, package and questionnaire editors (preview, publish with confirm) | `/admin/products/*`, `/admin/programs/*`, `/admin/packages/*`, `/admin/questionnaires/*` |
| Order detail and transitions, subscription detail, refund dialog | `/staff/orders/*`, `/staff/subscriptions/*`, `/staff/payments/*/refund/` |
| Content list, FAQ/testimonial managers, legal editor with versions, settings, audit log, report export | `/admin/content-items/*`, `/admin/faqs/`, `/admin/testimonials/`, `/admin/legal-documents/*`, `/admin/settings/{group}/`, `/admin/audit-logs/`, `/admin/reports/exports/` |
| Confirmation dialogs and empty states for every list | — |

## 13.4 Open items and reviews

Tracked in `docs/open-items.md` (Part 5 entries); every value is configurable
through `/admin/settings/{group}/` or environment settings.
