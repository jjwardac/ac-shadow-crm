-- Test companies deduplication
-- Shows duplicate companies grouped by company name (legal entity)

WITH latest_ingest AS (
  SELECT MAX(ingest_date) as max_date FROM `raw-external-prod`.`hubspot`.`companies`
),
company_normalized AS (
  SELECT
    hs_object_id,
    name,
    legal_entity_name,
    domain,
    linkedin_company_page,
    LOWER(TRIM(name)) as normalized_name,
    LOWER(TRIM(legal_entity_name)) as normalized_legal_entity,
    LOWER(TRIM(domain)) as normalized_domain,
    LOWER(TRIM(linkedin_company_page)) as normalized_linkedin,
    -- Strip "Demo -", "Unknown -", etc. prefixes, then extract base name
    LOWER(TRIM(REGEXP_REPLACE(
      REGEXP_REPLACE(legal_entity_name, '(?i)^(demo|unknown|project|test)[- ]+', ''),
      '(^.+?)\s*[-–—].*',
      '$1'
    ))) as base_legal_entity,
    -- Strip "Demo -", "Unknown -", then extract project/company name
    LOWER(TRIM(REGEXP_REPLACE(
      REGEXP_REPLACE(name, '(?i)^(demo|unknown|test)[- ]+', ''),
      '(?i)^project[- ]+([^-]+).*',
      '$1'
    ))) as extracted_company_name
  FROM `raw-external-prod`.`hubspot`.`companies`
  WHERE hs_object_id IS NOT NULL
    AND ingest_date = (SELECT max_date FROM latest_ingest)
),
company_groups AS (
  SELECT
    hs_object_id,
    name,
    legal_entity_name,
    domain,
    linkedin_company_page,
    COALESCE(
      -- Tier 1: extracted_company_name (primary identifier - what you're looking at)
      CASE 
        WHEN extracted_company_name IS NOT NULL AND extracted_company_name NOT IN ('', 'project', 'demo', 'unknown', 'test')
        THEN extracted_company_name
        ELSE NULL
      END,
      -- Tier 2: base_legal_entity (if no extracted name)
      CASE 
        WHEN base_legal_entity IS NOT NULL AND base_legal_entity NOT IN ('', 'project', 'demo', 'unknown', 'test')
        THEN base_legal_entity
        ELSE NULL
      END,
      -- Tier 3: Domain + base_legal_entity (secondary - for companies with multiple domains)
      CASE 
        WHEN normalized_domain IS NOT NULL AND normalized_domain != '' 
          AND base_legal_entity IS NOT NULL AND base_legal_entity NOT IN ('', 'project', 'demo', 'unknown', 'test')
        THEN CONCAT(normalized_domain, '|', base_legal_entity)
        ELSE NULL
      END,
      -- Tier 4: LinkedIn URL
      CASE 
        WHEN normalized_linkedin IS NOT NULL AND normalized_linkedin != ''
        THEN normalized_linkedin
        ELSE NULL
      END,
      -- Tier 5: Singleton (unique per company)
      CONCAT('singleton_', hs_object_id)
    ) as company_group_key
  FROM company_normalized
)
SELECT
  company_group_key,
  COUNT(*) as duplicate_count,
  COLLECT_LIST(legal_entity_name) as legal_entity_names,
  COLLECT_LIST(name) as names,
  COLLECT_LIST(domain) as domains,
  COLLECT_LIST(linkedin_company_page) as linkedin_urls,
  COLLECT_LIST(hs_object_id) as hubspot_ids
FROM company_groups
GROUP BY company_group_key
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC
LIMIT 50;
