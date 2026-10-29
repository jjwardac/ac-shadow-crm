-- Test contacts deduplication
-- Shows duplicate contacts grouped by matching key with confidence scores

WITH latest_ingest AS (
  SELECT MAX(ingest_date) as max_date FROM `raw-external-prod`.`hubspot`.`contacts`
),
contact_normalized AS (
  SELECT
    hs_object_id,
    firstname,
    lastname,
    email,
    hs_linkedin_url,
    jobtitle,
    LOWER(TRIM(email)) as normalized_email,
    LOWER(TRIM(firstname)) as normalized_firstname,
    LOWER(TRIM(lastname)) as normalized_lastname,
    LOWER(TRIM(hs_linkedin_url)) as normalized_linkedin,
    CONCAT(LOWER(TRIM(firstname)), ' ', LOWER(TRIM(lastname))) as normalized_full_name
  FROM `raw-external-prod`.`hubspot`.`contacts`
  WHERE hs_object_id IS NOT NULL
    AND ingest_date = (SELECT max_date FROM latest_ingest)
),
contact_groups AS (
  SELECT
    hs_object_id,
    firstname,
    lastname,
    email,
    hs_linkedin_url,
    jobtitle,
    CASE
      WHEN normalized_email IS NOT NULL AND normalized_linkedin IS NOT NULL 
        THEN 1.0
      WHEN normalized_email IS NOT NULL 
        THEN 0.95
      WHEN normalized_linkedin IS NOT NULL 
        THEN 0.90
      WHEN normalized_firstname IS NOT NULL AND normalized_lastname IS NOT NULL 
        THEN 0.70
      ELSE 0.5
    END as confidence_score,
    CASE
      WHEN normalized_email IS NOT NULL AND normalized_linkedin IS NOT NULL 
        THEN 'email + linkedin match'
      WHEN normalized_email IS NOT NULL 
        THEN 'email match'
      WHEN normalized_linkedin IS NOT NULL 
        THEN 'linkedin match'
      WHEN normalized_firstname IS NOT NULL AND normalized_lastname IS NOT NULL 
        THEN 'first and last name match'
      ELSE 'incomplete data'
    END as confidence_reasoning,
    COALESCE(
      normalized_email,
      normalized_linkedin,
      normalized_full_name,
      CONCAT('singleton_', hs_object_id)
    ) as contact_group_key
  FROM contact_normalized
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
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC, avg_confidence DESC
LIMIT 20;
