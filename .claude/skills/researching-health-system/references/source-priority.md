# Source Priority — Health System Research

## Primary Databases (check first)

### CMS Care Compare
URL: https://www.medicare.gov/care-compare/
- Bed count, ownership type, CMS star rating, hospital type
- Search by hospital name or address
- Direct URL pattern: https://www.medicare.gov/care-compare/details/Hospital/[provider-id]

### CMS Hospital General Information (bulk data)
URL: https://data.cms.gov/provider-data/dataset/xubh-q36u
- Downloadable CSV with all US hospitals
- Fields: facility name, address, state, type, ownership, bed count

## By Field

### health_system (parent affiliation)
1. Hospital website — footer, about page, "part of [system]" language
2. CMS Care Compare — system affiliation field
3. AHA (American Hospital Association) annual survey data (if accessible)
4. News coverage — acquisition/merger announcements

### bed_count
1. CMS Care Compare — licensed bed count
2. Hospital website — about/facts page ("X-bed hospital")
3. State department of health facility database
4. Note: "licensed beds" vs "staffed beds" vs "available beds" — prefer licensed

### ownership_type
1. CMS Care Compare — ownership field
2. IRS EO Select Check (for 501(c)(3) status): https://apps.irs.gov/app/eos/
3. Hospital website — governance/board page

### ehr_vendor
1. Hospital website — patient portal login page (Epic MyChart, Oracle Health,
   Meditech Expanse are identifiable by branding)
2. Press releases announcing EHR go-live or contract
3. Healthcare IT News, Becker's Health IT — EHR selection/go-live coverage
4. KLAS Research public profiles (accessible via web search)
5. Epic's "Find a Doctor" or "MyChart" pages list Epic customers

### cms_star_rating
1. CMS Care Compare — star rating displayed on hospital profile
2. URL: https://www.medicare.gov/hospitalcompare/search.html
3. Note the rating year in research_notes (ratings update annually)

### teaching_hospital
1. COTH directory: https://www.aamc.org/what-we-do/mission-areas/medical-education/coth
2. Hospital website — GME/residency programs page
3. ACGME (Accreditation Council for Graduate Medical Education) program search

### vbc_participation
1. CMS MSSP ACO participants:
   https://data.cms.gov/medicare-shared-savings-program/accountable-care-organizations
2. CMS BPCI-Advanced participants:
   https://innovation.cms.gov/innovation-models/bpci-advanced
3. Hospital website — population health or value-based care section
4. Press releases about ACO formation or CMS program participation

### payer_mix, annual_revenue
1. ProPublica Nonprofit Explorer (IRS 990s): https://projects.propublica.org/nonprofits/
   — Search by hospital name; Schedule H shows payer mix breakdown
2. Hospital annual report (PDF) — usually on investor relations or about page
3. Audited financial statements — sometimes posted by state health departments
4. Note: for-profit hospitals file 10-K with SEC, which includes revenue

### innovation_program
1. AVIA member list: https://www.aviahealth.com/members/
2. Hospital website — innovation center, digital health, or partnerships page
3. Press releases about innovation partnerships or accelerator programs
4. Becker's Health IT — "innovation" coverage

### recent_tech_announcements
1. Hospital newsroom / press release page
2. Business Wire / PR Newswire — search "[hospital name] technology"
3. Becker's Health IT, Healthcare IT News, HIMSS news
4. Limit to last 2 years; note the announcement date in the value string

### cio_name
1. LinkedIn — search "[hospital name] Chief Information Officer"
2. Hospital website — leadership/executive team page
3. Becker's Health IT "CIO" articles and lists
4. HIMSS conference speaker listings

### geographic_region
1. Derived from the hospital's state — use the mapping in field-definitions.md
2. CMS Care Compare — address/state field
3. No web search needed once state is known

## General Guidance

- CMS Care Compare is the single most reliable starting point for any US hospital
- For non-profit hospitals, the IRS 990 (via ProPublica) is the authoritative financial source
- For for-profit hospitals, SEC 10-K filings (parent company) are authoritative
- When a hospital is part of a large system, some data (EHR, revenue) may only be
  findable at the system level — report at system level and note in research_notes
- Critical access hospitals (<25 beds, rural) have different CMS reporting requirements;
  many fields will be null
