#!/usr/bin/env python3
"""
Daily job to deduplicate HubSpot companies and contacts into shadow_crm.
Runs via Databricks job, populates shadow_crm.companies and shadow_crm.contacts
with canonical IDs and HubSpot record mappings.
"""

import argparse
import logging
from databricks.sdk import WorkspaceClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def deduplicate_companies(catalog: str, schema: str) -> int:
    """Deduplicate companies and insert into shadow_crm.companies."""
    
    w = WorkspaceClient()
    
    query = f"""
    -- Deduplicate companies from HubSpot
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
    INSERT INTO `{catalog}`.`{schema}`.companies
    SELECT
      MD5(company_group_key) as canonical_company_id,
      COLLECT_LIST(hs_object_id) as hubspot_ids,
      FIRST(name) as name,
      FIRST(legal_entity_name) as legal_entity_name,
      FIRST(domain) as domain,
      FIRST(linkedin_company_page) as linkedin_url,
      CURRENT_TIMESTAMP() as created_at,
      CURRENT_TIMESTAMP() as updated_at
    FROM company_groups
    GROUP BY company_group_key
    """
    
    logger.info("Running company deduplication...")
    response = w.queries.query(sql=query)
    result = response.get_result()
    
    rows_affected = result.row_count if result and result.row_count else 0
    logger.info(f"Inserted {rows_affected} deduplicated companies")
    
    return rows_affected


def deduplicate_contacts(catalog: str, schema: str) -> int:
    """Deduplicate contacts and insert into shadow_crm.contacts."""
    
    w = WorkspaceClient()
    
    query = f"""
    -- Deduplicate contacts from HubSpot
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
    INSERT INTO `{catalog}`.`{schema}`.contacts
    SELECT
      MD5(contact_group_key) as canonical_contact_id,
      COLLECT_LIST(hs_object_id) as hubspot_ids,
      FIRST(email) as email,
      FIRST(firstname) as first_name,
      FIRST(lastname) as last_name,
      FIRST(hs_linkedin_url) as linkedin_url,
      CAST(FIRST(jobtitle) AS STRING) as job_title,
      CAST(AVG(confidence_score) AS DECIMAL(3,2)) as confidence_score,
      FIRST(confidence_reasoning) as confidence_reasoning,
      CURRENT_TIMESTAMP() as created_at,
      CURRENT_TIMESTAMP() as updated_at
    FROM contact_groups
    GROUP BY contact_group_key
    """
    
    logger.info("Running contact deduplication...")
    response = w.queries.query(sql=query)
    result = response.get_result()
    
    rows_affected = result.row_count if result and result.row_count else 0
    logger.info(f"Inserted {rows_affected} deduplicated contacts")
    
    return rows_affected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="revops-prod", help="Catalog containing shadow_crm schema")
    parser.add_argument("--schema", default="shadow_crm", help="Schema for shadow_crm tables")
    args = parser.parse_args()
    
    logger.info(f"Starting shadow_crm deduplication: {args.catalog}.{args.schema}")
    
    companies = deduplicate_companies(args.catalog, args.schema)
    contacts = deduplicate_contacts(args.catalog, args.schema)
    
    logger.info(f"Completed: {companies} companies, {contacts} contacts")


if __name__ == "__main__":
    main()
