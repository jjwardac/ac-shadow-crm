-- Test companies deduplication
-- Shows duplicate companies grouped by matching key

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
    CASE 
      WHEN LOWER(TRIM(name)) LIKE '%project%' OR LOWER(TRIM(name)) LIKE '%deal%' 
      THEN LOWER(REGEXP_REPLACE(TRIM(name), '(project|deal|engagement)[- ]?', ''))
      ELSE LOWER(TRIM(name))
    END as normalized_name_without_project
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
      CASE 
        WHEN normalized_domain IS NOT NULL AND normalized_legal_entity IS NOT NULL 
        THEN CONCAT(normalized_domain, '|', normalized_legal_entity)
        ELSE NULL
      END,
      CASE 
        WHEN normalized_domain IS NOT NULL AND normalized_name_without_project IS NOT NULL 
        THEN CONCAT(normalized_domain, '|', normalized_name_without_project)
        ELSE NULL
      END,
      CASE 
        WHEN normalized_legal_entity IS NOT NULL 
        THEN normalized_legal_entity
        ELSE NULL
      END,
      CASE 
        WHEN normalized_linkedin IS NOT NULL 
        THEN normalized_linkedin
        ELSE NULL
      END,
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
LIMIT 20;
