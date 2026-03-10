# Source Priority — Health IT Vendor Research

## By Field

### product_category, primary_customer, business_model
1. Company website — product/solutions pages, pricing page, homepage tagline
2. G2 or Capterra listing — often has category tags
3. Press releases — funding announcements often describe the product
4. News coverage — MedCity News, Fierce Healthcare, Healthcare IT News

### ehr_integrations
1. Company website — integrations/partners page, "works with" section
2. Epic App Orchard — search for the company by name
3. Oracle Health marketplace
4. Press releases announcing specific EHR partnerships

### notable_health_system_customers
1. Company website — customers/case studies page, logos on homepage
2. Press releases on PR Newswire, Business Wire, GlobeNewswire
3. News coverage — "X health system partners with Y" announcements
4. Do NOT include customers mentioned only in paid/sponsored content

### fda_status
1. FDA 510(k) database: https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm
2. FDA De Novo database: https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/denovo.cfm
3. FDA PMA database: https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpma/pma.cfm
4. Company website — regulatory/compliance section
5. Press releases announcing clearance

### clinical_evidence
1. PubMed: https://pubmed.ncbi.nlm.nih.gov — search "[company name] OR [product name]"
2. Google Scholar — same search terms
3. Company website — research/evidence page (verify publications are peer-reviewed)
4. NEJM Catalyst, JAMIA, Applied Clinical Informatics are high-quality target journals

### funding_stage, total_funding, key_investors
1. Crunchbase: https://www.crunchbase.com/organization/[company-slug]
2. SEC EDGAR: https://www.sec.gov/cgi-bin/browse-edgar — S-1, S-1/A, 10-K for public companies
3. Press releases — individual funding round announcements
4. PitchBook (if accessible via web search results)
5. LinkedIn company page — sometimes lists funding info

### num_employees
1. LinkedIn company page — shows employee count range
2. Company website — careers page headcount claims
3. Crunchbase — employee count field
4. Note: LinkedIn tends to be most current; use as primary

### headquarters
1. Company website — contact/about page, footer address
2. Crunchbase — headquarters field
3. LinkedIn — company location field
4. SEC filings — registered address (for public companies)

### founded_year
1. Crunchbase — founded year field
2. Company website — "Founded in XXXX" on about page
3. LinkedIn — company founded year
4. SEC filings — earliest filing date as a lower bound

## General Guidance

- Always prefer a direct URL fetch over inferring from search snippets
- When two sources conflict, prefer the more authoritative source and note the
  discrepancy in research_notes
- For recently founded companies (<3 years), Crunchbase and LinkedIn may be
  more current than news coverage
- For public companies, SEC EDGAR filings (10-K annual reports) are authoritative
  for revenue, employee count, and business model description
