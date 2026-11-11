# Figma review record — API Contract Part 1 (v1.2)

Source: Figma file `ck6is4EPRiiQKNclaaFCFG` (the copy). This file records the
design/copy state that accompanied contract v1.2. It has no API surface of its
own; the endpoint links below point at the contract sections implemented in this
repository.

## 1. Fixed in the Figma copy (19 text nodes)

| Fix | Where |
| --- | --- |
| Brand leftovers "Vitalis" replaced with "Adelgaceya" | Dashboard welcome text on 5 admin/patient screens, Terms of Service subtitle, Consultations subtitle (2 screens), consultation room label (2 screens) |
| Email domains `vitalishealth.com`, `vitalistelehealth.com`, `vitalis.health` replaced with `adelgaceya.pe` | Contact form placeholder, a physician form card, the "Add staff" example placeholder. `adelgaceya.pe` is taken from the logo; the real email domain still needs confirmation. |
| Login password placeholder "Create a password" changed to "Password" | All 5 login frames (MacBook Air 66, 67, 68, 69, 70). The signup screen keeps "Create a password". |
| Contact Us name field showed the pre-filled value "Marcus Vance" | Now the placeholder "e.g. Maria Gonzalez". |

## 2. Figma text still to change by hand (11 text nodes)

These use fonts that are not installed in the Figma file (Basier Square, Noah
Head Bold, Liberation Mono), so they could not be edited from the review tool.

| Node | Screen | Change to |
| --- | --- | --- |
| `1:461` | Landing footer | `contact@adelgaceya.pe` |
| `1:466` | Landing footer | `+51 1 555 0100` |
| `1:470` | Landing footer | `Av. Larco 1234, Miraflores, Lima 15074, Perú` |
| `1:493` | Landing footer | "Complains Book" -> "Complaints Book" |
| `1:302`, `1:357`, `1:339` | Landing testimonials | Replace "Vitalis" with "Adelgaceya" |
| `1:3295`, `1:3321`, `1:3344` | Staff list (admin) | `@vitalis.health` -> `@adelgaceya.pe` |

## 3. Added screens (designed below the existing canvas at y = 17795)

The `1:` prefix is omitted in the node IDs. These reuse the login card, fonts
and colors.

| Node | Screen | Backend contract |
| --- | --- | --- |
| `2005:12` | Forgot password | `POST /auth/password-reset/` (1.8) |
| `2005:64` | Reset link sent | 1.8 (always `204`) |
| `2005:121` | Set new password | `POST /auth/password-reset/confirm/` (1.9) |
| `2005:198` | Password updated | 1.9 |
| `2005:254` | Verify email | `POST /auth/verify-email/` (1.6) |
| `2005:311` | Email verified | 1.6 |
| `2005:367` | Link expired | 1.6 / 1.9 token errors |

The "Reset link sent" screen was checked visually and the subtitle wrapping was
fixed on all seven screens; they were not re-checked visually afterwards because
the Figma tool limit was reached.

## 4. Screens that are not designed yet

| Screen | What it must show | Contract link |
| --- | --- | --- |
| "Forgot password?" link on Login (`1:6829`) | Right-aligned link between the password field and the Login button | 1.8 |
| Consent checkbox on Signup (`1:6746`) | Checkbox above "Create Account": "I agree to the Terms of Service and Privacy Policy" (both linked), including an unchecked error state | 1.1 `consents` |
| Complaints Book success | Complaint number (e.g. `LR-2026-000123`), status, response due date, "Back to home" | 5.1 response |
| Contact form success | "Message sent" confirmation | 4.1 |
| Contact form error | Inline validation errors and a "Something went wrong" banner | 4.1, error envelope |
| Complaints Book error | Same pattern as contact error | 5.1 |

## 5. Content decisions (not changed in Figma)

1. Footer phone and address use the v1.2 demo values; replace them with real
   data before launch.
2. Terms of Service mention "state-regulated 503A/503B partner pharmacies" (a
   US regime), "approved service jurisdiction" and "verified ID". Confirm the
   Peruvian regulatory wording and whether ID verification is in scope.
3. The "100% money-back guarantee" needs legal sign-off; the refund flow belongs
   to Part 3 (orders).
4. Complaints Book dropdown options and Peruvian required fields are undefined
   (contract 3.5 and 5.1).
5. Marketing statistics (94%, 50k+, 14 min) and the 4.92 rating need evidence;
   testimonials need recorded patient consent (contract 3.3 and 3.4).
6. Supported languages (`es`, `en`) need confirmation. The UI is English with
   Spanish terms in the Complaints Book.
7. The 24-hour response promise on the Complaints Book is an operational SLA.

## 6. Found while reviewing, for Part 2

- The patient Overview stepper skips step 4 (3 -> 5).
- The Overview projection shows 200 lbs (82 kg) and 158 lbs (70 kg). 200 lbs is
  about 91 kg and 158 lbs is about 72 kg; "-42 lbs (-12 kg)" is also
  inconsistent. The Body Measurements screen uses 82 kg -> 70 kg, so the lbs
  values should be about 181 and 154 lbs, with a loss of about 26–27 lbs.
- The "Date of birth" card on Body Measurements contains Male/Female/Other
  buttons instead of a date input.

These are frontend/design fixes and are tracked in `docs/open-items.md`.
