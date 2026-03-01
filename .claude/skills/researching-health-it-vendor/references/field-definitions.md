# Field Definitions — Health IT Vendor

## product_category
Primary product type. Choose the single best fit:

| Value | Description |
|---|---|
| AI Scribe | Ambient documentation, clinical note generation |
| EHR | Electronic health record platform |
| RCM | Revenue cycle management, billing, coding |
| Care Management | Population health, chronic disease, case management |
| CDT | Clinical decision support tools |
| Patient Engagement | Portals, scheduling, communication, wayfinding |
| Clinical Decision Support | Alerts, order sets, evidence-based guidance at point of care |
| Interoperability | FHIR APIs, HIE, data exchange infrastructure |
| Other | Anything not fitting above |

If the product spans multiple categories, choose the one generating the most revenue
or the one most prominently featured on the website.

## primary_customer
The primary buyer segment — who writes the check:

| Value | Description |
|---|---|
| Provider | Hospitals, health systems, physician groups, clinics |
| Payer | Health insurance companies, managed care organizations |
| Employer | Self-insured employers, benefits administrators |
| DTC | Direct-to-consumer; individuals pay out of pocket |

## business_model
How the company charges customers:

| Value | Description |
|---|---|
| SaaS | Flat subscription fee (annual or monthly), typically per-site or per-facility |
| Per-Seat | Fee per licensed user (clinician, staff member) |
| PMPM | Per-member-per-month; common in population health and payer contracts |
| Implementation Fee | One-time project fees; common in EHR and large enterprise deals |
| Usage-Based | Charged per transaction, per encounter, per API call |
| Other | Hybrid models or models not fitting above |

## fda_status
Regulatory classification for medical devices / SaMD (Software as a Medical Device):

| Value | Description |
|---|---|
| Not Required | Software is not a medical device; FDA oversight not applicable |
| Cleared | 510(k) clearance granted |
| Breakthrough Device | FDA Breakthrough Device Designation granted (not yet cleared/approved) |
| PMA | Pre-Market Approval granted (highest-risk devices) |
| Pending | Application submitted, decision pending |
| Unknown | Cannot determine from available sources |

If the company has multiple products at different stages, report the status of
the primary/flagship product.

## clinical_evidence
Boolean: **true** if at least one peer-reviewed study has been published validating
the product's clinical outcomes, accuracy, or safety. False otherwise.

Acceptable sources: PubMed-indexed journals, NEJM Catalyst, JAMIA, Applied Clinical
Informatics. Conference abstracts alone do not count.

## funding_stage
Most recent completed funding round or financing status:

| Value | Description |
|---|---|
| Seed | Pre-Series A; angel, pre-seed, seed rounds |
| Series A | First institutional venture round |
| Series B | Second institutional round |
| Series C | Third institutional round |
| Series D+ | Four or more venture rounds |
| Public | IPO completed; trades on public exchange |
| Profitable | Bootstrapped or profitable with no outside venture capital |
| Unknown | Cannot determine |

Use the most recently *completed* round. Announced but not closed rounds are Unknown.

## confidence levels
- **high**: Data from a primary authoritative source (company website, SEC filing,
  FDA database, Crunchbase with named round and date)
- **medium**: Data from a secondary source (press release, news article, LinkedIn)
  that explicitly states the value
- **low**: Inferred, estimated, or from a single unnamed/unreliable source
- Use **null** (not low confidence) when you cannot find the value at all
