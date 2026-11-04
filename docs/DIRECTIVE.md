# Government advertisement directive

Source: https://e-rasaneh.ir/Page_Show.aspx?PageID=24
Directive 228604, 1402/08/22, amended 1403/04/02; tariff baseline: 1403.

## Setup

Back up the instance and run `python -m flask --app app migrate`. Schema 6 adds
`directive_record`; existing advertising records and attachments remain intact.
Open **دستورالعمل و سفارش‌ها** in the sidebar. This is an internal workspace:
staff record official e-rasaneh identifiers, dispatch references and authority
decisions. It does not transmit orders or correspondence to external systems.

1. Complete media profiles, including licensing, insurance, quality, coverage,
   headquarters, authorized branches, ownership review and eligibility dates.
   Profile identifiers are existing outlet IDs. Verify profiles with a dated
   authority reference. Attach supporting evidence; these downloads require login.
2. Confirm the office province/city, official holiday calendar and applicable
   tariff. Friday is excluded from working days. The baseline 1403 rate schedule
   requires explicit confirmation; later approved schedules are effective-dated,
   immutable records with whole-rial amounts.
3. Classify current-year historical recipients before activation. Activation
   records their priority classification without certifying historical compliance
   or changing historical prices. New allocations require verified eligibility.
4. Register the issuing organization in the ordinary organization catalog.
   Create an order, upload its Word source, and progress through review. Add
   placements using the internal order ID displayed in the table. Each placement
   selects an outlet, repetitions, billing category and page group.
5. Allocate the order. This creates actual advertisement records transactionally;
   repeated submission of the allocated stage must not create more records.
   Numerical exceptions require an administrator's documented decision.
6. Record official dispatch, publication and invoice evidence. Enter actual
   publication specifications on placements before verification. Certification
   freezes the calculation breakdown and a Persian-calendar one-month due date.
   Record payment evidence to close the order. Corrections to certified orders
   require a new amendment record rather than overwriting their certificate.

## Coverage and responsibility

| Directive | Application behavior / recorded official responsibility |
| --- | --- |
| 1–2 | Order subject, issuing organization, verified media and official dispatch references; ministry authorization remains external. |
| 3–6 | Eligibility evidence, threshold checks, printed/online ranks, ownership attestation, provincial online top-five limit, effective tariff records. |
| 7–9 | Provincial priorities; default 65% recommendation within 60–70%; equivalent-peer volume findings; eligible-month normalization; 30% headquarters weighting; recorded numerical exceptions; named-media additional-recipient check. |
| 10 | Provincial/city supplement orders rejected. |
| 11, appendices | Independent orders, representative metadata, Word evidence, requested pages and three-working-day lead checks. |
| 12–13 | Certificate snapshot, payment reference, one-month due date; overdue correspondence and credit restriction are recorded under obligations. |
| 14–16 | Official dispatch and publication evidence; online permanent URL or printed-copy reference recorded by staff. |
| 17–23 | Effective-dated official sanctions, private documentary evidence and obligation records for copy delivery, archive retention, coordination and regulatory correspondence. Physical delivery and third-party website availability are verified by staff. |
| 24 | All 20 finding categories, fixed deductions and ancillary-cost caps; administrator approves the net monetary adjustment. Three approved reduction findings within a year exclude the outlet for two months. |
| 25–27 | Exact-rial rate calculator, actual publication size/page/headline fields, preserved certification snapshot. Enter one headline box where requested without a stated volume. |
| 28–31 | Official-reference evidence before dispatch, direct-ad sanctions/deduction metadata, and authority correspondence/credit-block obligation records. |
| 32 | Public quarterly Excel endpoint `/reports/directive-quarter?year=1405&quarter=1`. |
| 33–38 | Separate government/company/civil/criminal billing categories and official legal interpretations recorded with supporting evidence. |

The 65% selection target, monthly partial-day normalization and deterministic
candidate ordering are operational conventions. They do not replace official
interpretation. Unknown or disputed legal cases require a recorded authority
decision. The app does not issue an official ministry certificate, verify external
licenses online, perform payments, or operate an external publisher's archive.

## Verification

Server/calendars: `.venv/bin/python scripts/run_tests.py`.
Browser: `SOBHE_BROWSER_CHANNEL=chrome npm run test:browser` uses installed Chrome
with disposable test data and a separate temporary browser profile.
