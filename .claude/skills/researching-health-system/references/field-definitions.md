# Field Definitions — Health System

## ownership_type

| Value | Description |
|---|---|
| Non-profit | 501(c)(3) tax-exempt organization; most community and academic hospitals |
| For-profit | Investor-owned; HCA Healthcare, Tenet Health, Community Health Systems |
| Academic | University-owned or tightly affiliated medical center; may overlap with Non-profit |
| Government | Federal (VA, DoD), state, or county/municipal hospital |
| Unknown | Cannot determine from available sources |

If a hospital is a non-profit academic medical center, use **Academic** — it is the
more specific and more useful category for BD purposes.

## ehr_vendor
Primary EHR platform for inpatient clinical documentation and orders:

| Value | Notes |
|---|---|
| Epic | Verona, WI; dominant in large academic and integrated health systems |
| Oracle Health | Formerly Cerner; strong in community hospitals and VA |
| Meditech | Common in critical access and smaller community hospitals |
| Allscripts | Now Veradigm; declining install base |
| athenahealth | Primarily ambulatory/outpatient; rare as primary inpatient EHR |
| Other | Any other platform (CPSI, Netsmart, etc.) |
| Unknown | Cannot determine |

If the hospital is part of a system that standardized on one EHR, that is the answer
even if individual facilities are still in transition.

## cms_star_rating
CMS Overall Hospital Quality Star Rating — 1 to 5 stars, published on Care Compare.

- Critical Access Hospitals (CAHs) are typically **not rated** — use null
- New hospitals (<1 year of data) are **not rated** — use null
- Ratings update annually; note the date in research_notes if it seems outdated

## teaching_hospital
True if:
- Listed in the COTH (Council of Teaching Hospitals and Health Systems) directory, OR
- The hospital explicitly identifies as an academic medical center with active
  graduate medical education (residency/fellowship programs), OR
- Has a formal medical school affiliation with clinical training programs

False if none of the above apply. Null only if you genuinely cannot determine.

## vbc_participation
True if the hospital or its parent system is:
- A participant in a CMS ACO (Accountable Care Organization) program
  (MSSP, ACO REACH, or predecessor programs), OR
- Participating in a CMS bundled payment program
  (BPCI-Advanced, Comprehensive Care for Joint Replacement, etc.)

Check: https://data.cms.gov/medicare-shared-savings-program/
Check: https://innovation.cms.gov/innovation-models/bpci-advanced

## payer_mix
Approximate revenue breakdown by payer type. Format as a string:
"45% Medicare, 22% Medicaid, 33% Commercial"

Source from IRS 990 (Schedule H for non-profits) or hospital annual report.
If only Medicare + Medicaid % is available, note that: "67% government (Medicare/Medicaid)"
Null if no breakdown findable.

## innovation_program
True if:
- Listed as an AVIA member (https://www.aviahealth.com/members/), OR
- Has a named internal innovation center or digital health accelerator, OR
- Has a formal startup partnership / pilot program publicly described on their website

## geographic_region
Map by state:
- **Northeast**: ME, NH, VT, MA, RI, CT, NY, NJ, PA
- **Southeast**: DE, MD, DC, VA, WV, NC, SC, GA, FL, KY, TN, AL, MS, AR, LA
- **Midwest**: OH, MI, IN, WI, IL, MN, IA, MO, ND, SD, NE, KS
- **Southwest**: OK, TX, NM, AZ
- **West**: CO, WY, MT, ID, WA, OR, CA, NV, UT, AK, HI

## confidence levels
- **high**: CMS Care Compare, IRS 990, official hospital annual report, hospital website
- **medium**: Press release, Becker's Health IT article, Healthcare IT News with explicit claim
- **low**: Inferred from indirect signals (e.g., guessing EHR from patient portal branding)
- Use **null** (not low confidence) when the value cannot be found at all
