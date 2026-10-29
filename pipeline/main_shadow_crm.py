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
          CASE 
            WHEN extracted_company_name IS NOT NULL AND extracted_company_name NOT IN ('', 'project', 'demo', 'unknown', 'test')
            THEN extracted_company_name
            ELSE NULL
          END,
          CASE 
            WHEN base_legal_entity IS NOT NULL AND base_legal_entity NOT IN ('', 'project', 'demo', 'unknown', 'test')
            THEN base_legal_entity
            ELSE NULL
          END,
          CASE 
            WHEN normalized_domain IS NOT NULL AND normalized_domain != '' 
              AND base_legal_entity IS NOT NULL AND base_legal_entity NOT IN ('', 'project', 'demo', 'unknown', 'test')
            THEN CONCAT(normalized_domain, '|', base_legal_entity)
            ELSE NULL
          END,
          CASE 
            WHEN normalized_linkedin IS NOT NULL AND normalized_linkedin != ''
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
    -- Deduplicate contacts from HubSpot with company context for name-only matches
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
