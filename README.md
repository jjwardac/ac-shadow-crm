# Shadow CRM

Deduplicate HubSpot companies and contacts into a unified canonical record system for enrichment and record mapping.

## Overview

Our CRM creates multiple Company records for the same entity rather than using a clear Deal/Subscription hierarchy, resulting in duplicate contacts as well. This project creates a `shadow_crm` schema in `revops-prod` with:

- **Canonical companies** — one record per unique entity (matched by domain, legal entity name, LinkedIn URL, normalized project/deal names)
- **Canonical contacts** — one record per unique person (matched by email, LinkedIn URL, normalized name)
- **HubSpot ID mappings** — all related HubSpot record IDs for each canonical record
- **Confidence scores** — for contacts, based on match quality

Once enriched, data syncs back to all related HubSpot records via Clay or similar tools.

## Setup

### 1. Schema & Tables

Create the `shadow_crm` schema in `revops-prod` via Terraform (in `infrastructure-databricks`):

```sql
CREATE SCHEMA `revops-prod`.`shadow_crm`;

-- Companies: one row per deduplicated company entity
CREATE TABLE `revops-prod`.`shadow_crm`.`companies` (
  canonical_company_id STRING PRIMARY KEY,
  hubspot_ids ARRAY<BIGINT>,
  name STRING,
  legal_entity_name STRING,
  domain STRING,
  linkedin_url STRING,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

-- Contacts: one row per deduplicated contact entity
CREATE TABLE `revops-prod`.`shadow_crm`.`contacts` (
  canonical_contact_id STRING PRIMARY KEY,
  hubspot_ids ARRAY<BIGINT>,
  email STRING,
  first_name STRING,
  last_name STRING,
  linkedin_url STRING,
  job_title STRING,
  confidence_score DECIMAL(3,2),  -- 0.0-1.0
  confidence_reasoning STRING,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

-- Junction table: many-to-many company-contact relationships
CREATE TABLE `revops-prod`.`shadow_crm`.`company_contacts` (
  canonical_company_id STRING,
  canonical_contact_id STRING,
  PRIMARY KEY (canonical_company_id, canonical_contact_id)
);
```

### 2. Daily Job

Deploy `resources/shadow_crm_sync.job.yml` via Databricks Bundle to run deduplication daily at 2 AM Eastern.

## Deduplication Logic

### Companies

Matched by (in priority order):

1. **Domain + Legal Entity Name** (highest confidence)
2. **Domain + Normalized Name** (with project/deal names stripped)
3. **Legal Entity Name alone**
4. **LinkedIn Company Page**
5. **Singleton** (unique per company if no matches)

### Contacts

Matched by (in priority order):

| Match Type | Confidence | Criteria |
|---|---|---|
| Email + LinkedIn | 1.0 | Both fields present |
| Email only | 0.95 | Email field present |
| LinkedIn only | 0.90 | LinkedIn field present |
| First + Last name | 0.70 | Both name fields present |
| Incomplete data | 0.5 | Fallback |

## Files

- `pipeline/main_shadow_crm.py` — Daily job that populates shadow_crm tables
- `resources/shadow_crm_sync.job.yml` — Databricks job definition (2 AM Eastern)
- `queries/` — Test/reference queries for validation

## Testing

Test deduplication logic before running the job:

```sql
-- See queries/test_company_deduplication.sql and queries/test_contact_deduplication.sql
```

## Enrichment Apply-Back

Once canonical records are established:

1. Run enrichment on one canonical record in `shadow_crm`
2. Apply results to **all** related HubSpot record IDs
3. Sync via Clay, Zapier, or API back to HubSpot

## Future Work

- Populate `company_contacts` junction table from HubSpot contact-company associations
- Build enrichment workflow (research, validation, apply-back)
- Create audit trail for enrichment changes
