-- Test contacts deduplication with company context for name-only matches
-- Shows duplicate contacts grouped by matching key with confidence scores

WITH latest_contact_ingest AS (
  SELECT MAX(ingest_date) as max_date FROM `raw-external-prod`.`hubspot`.`contacts`
),
latest_company_ingest AS (
  SELECT MAX(ingest_date) as max_date FROM `raw-external-prod`.`hubspot`.`companies`
),
contact_normalized AS (
  SELECT
    c.hs_object_id,
    c.firstname,
    c.lastname,
    c.email,
    c.hs_linkedin_url,
    c.jobtitle,
    LOWER(TRIM(c.email)) as normalized_email,
    LOWER(TRIM(c.firstname)) as normalized_firstname,
    LOWER(TRIM(c.lastname)) as normalized_lastname,
    LOWER(TRIM(c.hs_linkedin_url)) as normalized_linkedin,
    CONCAT(LOWER(TRIM(c.firstname)), ' ', LOWER(TRIM(c.lastname))) as normalized_full_name
  FROM `raw-external-prod`.`hubspot`.`contacts` c
  WHERE c.hs_object_id IS NOT NULL
    AND c.ingest_date = (SELECT max_date FROM latest_contact_ingest)
),
contact_with_companies AS (
  SELECT
    cn.hs_object_id,
    cn.firstname,
    cn.lastname,
    cn.email,
    cn.hs_linkedin_url,
    cn.jobtitle,
    cn.normalized_email,
    cn.normalized_firstname,
    cn.normalized_lastname,
    cn.normalized_linkedin,
    cn.normalized_full_name,
    -- Collect all canonical company IDs this contact is associated with
    COLLECT_SET(comp.canonical_company_id) as associated_company_ids
  FROM contact_normalized cn
  LEFT JOIN `raw-external-prod`.`hubspot`.`contact_company_associations` cca
    ON cn.hs_object_id = cca.contact_id
    AND cca.ingest_date = (SELECT max_date FROM latest_company_ingest)
  LEFT JOIN (
    SELECT hs_object_id, MD5(COALESCE(
      LOWER(TRIM(REGEXP_REPLACE(
        REGEXP_REPLACE(legal_entity_name, '(?i)^(demo|unknown|project|test)[- ]+', ''),
        '(^.+?)\s*[-–—].*',
        '$1'
      ))), 'unknown'
    )) as canonical_company_id
    FROM `raw-external-prod`.`hubspot`.`companies`
    WHERE hs_object_id IS NOT NULL
      AND ingest_date = (SELECT max_date FROM latest_company_ingest)
  ) comp ON cca.company_id = comp.hs_object_id
  GROUP BY cn.hs_object_id, cn.firstname, cn.lastname, cn.email, cn.hs_linkedin_url, cn.jobtitle,
           cn.normalized_email, cn.normalized_firstname, cn.normalized_lastname, cn.normalized_linkedin, cn.normalized_full_name
),
contact_groups AS (
  SELECT
    hs_object_id,
    firstname,
    lastname,
    email,
    hs_linkedin_url,
    jobtitle,
    associated_company_ids,
    CASE
      WHEN normalized_email IS NOT NULL AND normalized_email != '' AND normalized_linkedin IS NOT NULL AND normalized_linkedin != '' 
        THEN 1.0
      WHEN normalized_email IS NOT NULL AND normalized_email != '' 
        THEN 0.95
      WHEN normalized_linkedin IS NOT NULL AND normalized_linkedin != '' 
        THEN 0.90
      WHEN normalized_firstname IS NOT NULL AND normalized_firstname != '' AND normalized_lastname IS NOT NULL AND normalized_lastname != '' 
        THEN 0.70
      ELSE 0.5
    END as confidence_score,
    CASE
      WHEN normalized_email IS NOT NULL AND normalized_email != '' AND normalized_linkedin IS NOT NULL AND normalized_linkedin != '' 
        THEN 'email + linkedin match'
      WHEN normalized_email IS NOT NULL AND normalized_email != '' 
        THEN 'email match'
      WHEN normalized_linkedin IS NOT NULL AND normalized_linkedin != '' 
        THEN 'linkedin match'
      WHEN normalized_firstname IS NOT NULL AND normalized_firstname != '' AND normalized_lastname IS NOT NULL AND normalized_lastname != '' 
        THEN 'first and last name match'
      ELSE 'incomplete data'
    END as confidence_reasoning,
    -- Matching hierarchy:
    -- Tier 1: Email (global, unique identifier)
    -- Tier 2: LinkedIn (global, unique identifier)
    -- Tier 3: First name + Last name + Company (scoped to same company)
    -- Tier 4: Singleton
    COALESCE(
      CASE 
        WHEN normalized_email IS NOT NULL AND normalized_email != ''
        THEN CONCAT('email_', normalized_email)
        ELSE NULL
      END,
      CASE 
        WHEN normalized_linkedin IS NOT NULL AND normalized_linkedin != ''
        THEN CONCAT('linkedin_', normalized_linkedin)
        ELSE NULL
      END,
      CASE 
        WHEN normalized_firstname IS NOT NULL AND normalized_firstname != '' 
          AND normalized_lastname IS NOT NULL AND normalized_lastname != ''
          AND SIZE(associated_company_ids) > 0
        THEN CONCAT('name_', normalized_full_name, '|company_', FIRST(associated_company_ids))
        ELSE NULL
      END,
      CASE 
        WHEN normalized_firstname IS NOT NULL AND normalized_firstname != '' 
          AND normalized_lastname IS NOT NULL AND normalized_lastname != ''
        THEN CONCAT('name_', normalized_full_name)
        ELSE NULL
      END,
      CONCAT('singleton_', hs_object_id)
    ) as contact_group_key
  FROM contact_with_companies
)
SELECT
  contact_group_key,
  COUNT(*) as duplicate_count,
  COLLECT_LIST(CONCAT(firstname, ' ', lastname)) as names,
  COLLECT_LIST(email) as emails,
  COLLECT_LIST(hs_linkedin_url) as linkedin_urls,
  COLLECT_LIST(hs_object_id) as hubspot_ids,
  CAST(AVG(confidence_score) AS DECIMAL(3,2)) as avg_confidence,
  FIRST(confidence_reasoning) as confidence_reasoning
FROM contact_groups
GROUP BY contact_group_key
ORDER BY duplicate_count DESC, avg_confidence DESC;
