"""
California AB 2013 compliance manifest generator.

Every commercial data transaction includes an auto-generated manifest
covering all 12 required data provenance disclosure fields.

Reference: California Assembly Bill 2013 (2024) -- Generative AI Training
Data Transparency Act.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# Museum source metadata for each Alexandria Aeternum dataset
DATASET_SOURCES: dict[str, dict[str, Any]] = {
    "met-museum": {
        "institution": "The Metropolitan Museum of Art",
        "collection_id": "met-open-access",
        "api_endpoint": "https://collectionapi.metmuseum.org/public/collection/v1",
        "geographic_origin": "New York, NY, USA",
        "date_range_start": "2017-02-07",
        "ip_status": "CC0 1.0 Universal Public Domain Dedication",
    },
    "smithsonian": {
        "institution": "Smithsonian Institution",
        "collection_id": "smithsonian-open-access",
        "api_endpoint": "https://api.si.edu/openaccess/api/v1.0",
        "geographic_origin": "Washington, D.C., USA",
        "date_range_start": "2020-02-25",
        "ip_status": "CC0 1.0 Universal Public Domain Dedication",
    },
    "nga": {
        "institution": "National Gallery of Art",
        "collection_id": "nga-open-data",
        "api_endpoint": "https://api.nga.gov",
        "geographic_origin": "Washington, D.C., USA",
        "date_range_start": "2021-04-13",
        "ip_status": "CC0 1.0 Universal Public Domain Dedication",
    },
    "rijksmuseum": {
        "institution": "Rijksmuseum",
        "collection_id": "rijksmuseum-api",
        "api_endpoint": "https://www.rijksmuseum.nl/api",
        "geographic_origin": "Amsterdam, Netherlands",
        "date_range_start": "2013-01-01",
        "ip_status": "CC0 1.0 Universal Public Domain Dedication",
    },
    "chicago": {
        "institution": "Art Institute of Chicago",
        "collection_id": "artic-api",
        "api_endpoint": "https://api.artic.edu/api/v1",
        "geographic_origin": "Chicago, IL, USA",
        "date_range_start": "2018-11-01",
        "ip_status": "CC0 1.0 Universal Public Domain Dedication",
    },
    "cleveland": {
        "institution": "Cleveland Museum of Art",
        "collection_id": "cma-open-access",
        "api_endpoint": "https://openaccess-api.clevelandart.org/api",
        "geographic_origin": "Cleveland, OH, USA",
        "date_range_start": "2019-01-23",
        "ip_status": "CC0 1.0 Universal Public Domain Dedication",
    },
    "paris-elite": {
        "institution": "Multiple Paris Institutions (Louvre, Orsay, Rodin, etc.)",
        "collection_id": "paris-elite-curated",
        "api_endpoint": "Multiple (see institution APIs)",
        "geographic_origin": "Paris, France",
        "date_range_start": "2020-01-01",
        "ip_status": "CC0 1.0 Universal Public Domain Dedication / Open License",
    },
}


def generate_ab2013_manifest(
    order: dict[str, Any],
    dataset_id: str | None = None,
) -> dict[str, Any]:
    """Generate an AB 2013 compliance manifest for a data transaction.

    Args:
        order: Order document from Firestore containing dataset_id, quantity,
               payment info, etc.
        dataset_id: Override dataset ID (uses order's dataset_id if None).

    Returns:
        Dictionary with ``json`` (structured) and ``markdown`` (human-readable)
        representations of the manifest.
    """
    ds_id = dataset_id or order.get("dataset_id", "unknown")
    source = DATASET_SOURCES.get(ds_id, {})
    quantity = order.get("quantity", 0)
    payment_amount = order.get("total_price", 0)
    payment_method = order.get("payment_method", "unknown")
    tier = order.get("pricing_tier", "unknown")

    now = datetime.now(timezone.utc).isoformat()

    manifest = {
        "manifest_version": "AB2013-v1.0",
        "generated_at": now,
        "order_id": order.get("order_id", ""),

        # 1. Data sources
        "data_sources": {
            "institution": source.get("institution", f"Alexandria Aeternum - {ds_id}"),
            "collection_id": source.get("collection_id", ds_id),
            "api_endpoint": source.get("api_endpoint", "N/A"),
            "provider": "Intelligence Aeternum (iaeternum.ai), a Metavolve Labs, Inc. project",
        },

        # 2. Number of data points
        "number_of_data_points": quantity,

        # 3. Types of data
        "data_types": [
            "High-resolution artwork images (JPEG/PNG, typically 2048-4096px)",
            "Golden Codex enrichment metadata (JSON, 8-section structured analysis)",
            "Museum catalog metadata (JSON, title/artist/date/medium/dimensions)",
            "Perceptual hash fingerprints (SHA-256 + pHash)",
        ],

        # 4. IP status
        "intellectual_property_status": {
            "source_images": source.get("ip_status", "CC0 1.0 Universal Public Domain Dedication"),
            "enrichment_metadata": "Copyright Metavolve Labs, Inc. 2025-2026. Licensed per transaction.",
            "museum_catalog_metadata": "CC0 1.0 Universal Public Domain Dedication",
            "note": "Original artworks are public domain. AI-generated enrichment metadata is proprietary.",
        },

        # 5. Commercial arrangement
        "commercial_arrangement": {
            "license_tier": tier,
            "payment_amount": payment_amount,
            "payment_method": payment_method,
            "currency": "USDC" if payment_method == "x402" else "USD",
            "license_type": "Per-transaction data access license",
        },

        # 6. PII declaration
        "pii_declaration": {
            "contains_pii": False,
            "explanation": (
                "Dataset contains only public domain artwork images and structured metadata "
                "about artworks (title, artist name, date, medium, dimensions). No photographs "
                "of living individuals. Artist names are historical public figures (pre-1900 for "
                "most collections). Zero PII, biometric, or personal data."
            ),
        },

        # 7. Synthetic data declaration
        "synthetic_data_declaration": {
            "contains_synthetic_data": True,
            "synthetic_components": [
                {
                    "field": "golden_codex enrichment metadata",
                    "generator": "Google Vertex AI (Gemini 2.5/3.0 Pro)",
                    "description": (
                        "8-section structured art analysis generated by Nova agent "
                        "using proprietary system instructions. Includes: identity, "
                        "visual DNA, technique, emotional resonance, art historical "
                        "context, contemporary relevance, collector notes, provenance."
                    ),
                    "labeled": True,
                    "label_field": "metadata.enrichment_source",
                },
            ],
            "non_synthetic_components": [
                "Source artwork images (digitized museum photographs)",
                "Museum catalog metadata (from institution APIs)",
                "Perceptual hash fingerprints (computed, not generated)",
            ],
        },

        # 8. Collection methodology
        "collection_methodology": {
            "method": "Automated API ingestion from public museum collection endpoints",
            "tools": [
                "Custom Python ingestion scripts per museum API",
                "Google Cloud Storage for archival",
                "Nova Agent (Vertex AI) for enrichment",
                "Atlas Agent (ExifTool) for metadata infusion",
            ],
            "human_curation": (
                "All datasets undergo human curation review for quality, relevance, "
                "and appropriate content before publication."
            ),
        },

        # 9. Date range of collection
        "date_range": {
            "collection_start": source.get("date_range_start", "2020-01-01"),
            "collection_end": "ongoing",
            "enrichment_date": "2025-01 through 2026-02",
            "note": "Original artworks span antiquity to early 20th century.",
        },

        # 10. Geographic origin
        "geographic_origin": {
            "data_source_location": source.get("geographic_origin", "Multiple countries"),
            "data_processing_location": "Google Cloud Platform, us-west1 (Oregon, USA)",
            "data_storage_location": "Google Cloud Storage, us-west1 (Oregon, USA)",
        },

        # 11. Known limitations
        "known_limitations": [
            "Image resolution varies by museum digitization program (typically 1024-8192px).",
            "Museum metadata may contain historical terminology or classifications that "
            "reflect the biases of their era of cataloging.",
            "AI enrichment metadata reflects model capabilities at time of generation and "
            "may contain analytical inaccuracies.",
            "Not all artworks in source museums are included -- only CC0/public domain works.",
            "Artist attribution follows museum records, which may be disputed or updated.",
            "Date attributions for older works may be approximate (e.g., 'circa 1650').",
        ],

        # 12. Contact information
        "contact": {
            "provider": "Metavolve Labs, Inc.",
            "email": "data@iaeternum.ai",
            "website": "https://iaeternum.ai",
            "api": "https://api.iaeternum.ai/v1",
            "address": "San Francisco, California, USA",
            "data_protection_officer": "Tad MacPherson (curator@golden-codex.com)",
        },
    }

    markdown = _manifest_to_markdown(manifest)

    return {
        "json": manifest,
        "markdown": markdown,
    }


def generate_eu_ai_act_article53_manifest(
    order: dict[str, Any],
    dataset_id: str | None = None,
) -> dict[str, Any]:
    """Generate an EU AI Act Article 53 compliance manifest.

    Maps Golden Codex provenance data to the mandatory public disclosure
    template required by Article 53 (Obligations for providers of
    general-purpose AI models). Effective August 2, 2025.

    This transforms our enterprise offering from a dataset into an automated
    legal indemnification tool — preventing potential fines up to EUR 15M
    or 3% of annual worldwide turnover.

    Reference: Regulation (EU) 2024/1689, Article 53(1)(d) — detailed summary
    of training data content including sources, scope, and curation methodology.
    """
    ds_id = dataset_id or order.get("dataset_id", "unknown")
    source = DATASET_SOURCES.get(ds_id, {})
    quantity = order.get("quantity", 0)
    now = datetime.now(timezone.utc).isoformat()

    manifest = {
        "manifest_version": "EU-AI-Act-Art53-v1.0",
        "regulation": "Regulation (EU) 2024/1689",
        "article": "Article 53(1)(d)",
        "generated_at": now,
        "order_id": order.get("order_id", ""),

        # Art 53(1)(d)(i) — Description of training data
        "training_data_description": {
            "dataset_name": f"Alexandria Aeternum — {source.get('institution', ds_id)}",
            "dataset_provider": "Intelligence Aeternum (iaeternum.ai), Metavolve Labs, Inc.",
            "total_data_points": quantity,
            "data_modalities": ["images (JPEG/PNG)", "structured metadata (JSON)"],
            "content_domain": "Visual art — paintings, sculptures, drawings, prints, photographs, decorative arts",
            "temporal_coverage": "Antiquity (c. 3000 BCE) to early 20th century",
            "geographic_coverage": "Global — artworks from European, American, Asian, African, and Oceanic traditions",
            "languages": ["en"],
        },

        # Art 53(1)(d)(ii) — Data sources
        "data_sources": {
            "primary_sources": [
                {
                    "name": source.get("institution", ds_id),
                    "type": "Museum Open Access API",
                    "url": source.get("api_endpoint", "N/A"),
                    "license": source.get("ip_status", "CC0 1.0"),
                    "access_method": "REST API automated ingestion",
                },
            ],
            "secondary_sources": [
                {"name": "Wikidata", "type": "Knowledge graph", "url": "https://www.wikidata.org", "purpose": "Authority linking (birth/death dates, movements, influences)"},
                {"name": "Wikipedia", "type": "Encyclopedia", "url": "https://en.wikipedia.org", "purpose": "Artist biographies and artwork descriptions"},
                {"name": "Getty ULAN", "type": "Authority file", "url": "https://www.getty.edu/research/tools/vocabularies/ulan/", "purpose": "Artist identity resolution"},
            ],
            "web_scraping_declaration": {
                "web_scraping_used": True,
                "scope": "Publicly accessible museum collection pages and Wikipedia articles only",
                "robots_txt_compliance": True,
                "opt_out_mechanism": "Contact data@iaeternum.ai to request exclusion",
            },
        },

        # Art 53(1)(d)(iii) — Data curation and filtering
        "curation_methodology": {
            "selection_criteria": [
                "Only CC0 / Public Domain works included",
                "Image resolution minimum 512px shortest edge",
                "Museum-verified attribution required",
                "No works by living artists without explicit consent",
            ],
            "filtering_applied": [
                "Duplicate detection via perceptual hashing (pHash)",
                "Quality filtering: corrupt/truncated images excluded",
                "Rights filtering: only CC0/Public Domain verified works",
                "Content filtering: no NSFW content in training set",
            ],
            "human_oversight": "All datasets undergo human curation review before publication",
            "quality_metrics": {
                "success_rate": "99.93% pipeline success rate",
                "metadata_completeness": "95%+ fields populated for curated tier",
            },
        },

        # Art 53(1)(d)(iv) — Synthetic data
        "synthetic_data_declaration": {
            "human_curated_tier": {
                "contains_synthetic": False,
                "description": "100% human-sourced data from museum APIs, Wikipedia, Wikidata, Getty ULAN. LLM used only for JSON structuring, not content generation.",
                "llm_role": "Gemini 2.0 Flash organizes existing human data into schema fields. No creative generation.",
            },
            "oracle_enhanced_tier": {
                "contains_synthetic": True,
                "synthetic_fields": [
                    "visual_analysis (composition, color palette, lighting, technique)",
                    "emotional_and_thematic_journey (primary/secondary emotions, mood, narrative arc)",
                    "symbolism_and_iconography (deep symbolic analysis, archetypal elements)",
                ],
                "generator": "Google Vertex AI (Gemini 2.5/3.0 Pro)",
                "labeled": True,
                "label_mechanism": "schemaVersion field distinguishes '1.0.0-curated' (human) from '1.0.0' (oracle). _upgrade_note fields mark synthetic additions.",
                "separability": "Tiers are delivered as separate JSON files. Buyers can use human-only tier exclusively.",
            },
        },

        # Art 53(1)(d)(v) — Personal data
        "personal_data": {
            "contains_personal_data": False,
            "dpla_assessment": "No personal data processed. Dataset contains only: artwork images (public domain, no living subjects), artist names (historical public figures, pre-1900 for majority), museum catalog metadata.",
            "gdpr_basis": "Not applicable — no personal data",
        },

        # Art 53(1)(d)(vi) — Copyright compliance
        "copyright_compliance": {
            "source_material_license": source.get("ip_status", "CC0 1.0"),
            "tdm_legal_basis": {
                "us": "Fair Use (17 U.S.C. § 107) — transformative use for AI training",
                "eu": "DSM Directive Article 4 — commercial TDM permitted, no opt-out detected",
                "uk": "NOT INCLUDED — UK commercial TDM prohibited under Section 29A CDPA 1988",
            },
            "opt_out_compliance": "All sources checked for TDM opt-out signals (robots.txt, HTTP headers). None detected for included institutions.",
            "enrichment_metadata_copyright": "Copyright 2025-2026 Metavolve Labs, Inc. Licensed per transaction.",
        },

        # Provider information
        "provider_information": {
            "legal_entity": "Metavolve Labs, Inc.",
            "jurisdiction": "State of California, United States",
            "contact_email": "data@iaeternum.ai",
            "data_protection_contact": "Tad MacPherson (curator@golden-codex.com)",
            "website": "https://iaeternum.ai",
        },

        # Compliance statement
        "compliance_statement": (
            "This manifest is generated in compliance with Article 53(1)(d) of "
            "Regulation (EU) 2024/1689 (EU AI Act). The dataset provider maintains "
            "records of all data sources, curation methodology, and synthetic data "
            "declarations as required for general-purpose AI model training transparency. "
            "This manifest may be presented to EU regulatory authorities upon request."
        ),
    }

    return {
        "json": manifest,
        "markdown": _article53_to_markdown(manifest),
    }


def _article53_to_markdown(m: dict[str, Any]) -> str:
    """Convert Article 53 manifest to human-readable markdown."""
    lines = [
        "# EU AI Act Article 53 — Training Data Transparency Manifest",
        "",
        f"**Regulation**: {m['regulation']}",
        f"**Article**: {m['article']}",
        f"**Generated**: {m['generated_at']}",
        f"**Order ID**: {m['order_id']}",
        "",
        "---",
        "",
        "## Training Data Description",
        f"- **Dataset**: {m['training_data_description']['dataset_name']}",
        f"- **Provider**: {m['training_data_description']['dataset_provider']}",
        f"- **Data points**: {m['training_data_description']['total_data_points']:,}",
        f"- **Domain**: {m['training_data_description']['content_domain']}",
        f"- **Temporal coverage**: {m['training_data_description']['temporal_coverage']}",
        f"- **Geographic coverage**: {m['training_data_description']['geographic_coverage']}",
        "",
        "## Data Sources",
    ]
    for src in m["data_sources"]["primary_sources"]:
        lines.append(f"- **{src['name']}** ({src['type']}): {src['url']} — {src['license']}")
    lines.append("")
    lines.append("### Secondary Sources")
    for src in m["data_sources"]["secondary_sources"]:
        lines.append(f"- **{src['name']}**: {src['purpose']}")
    lines.extend([
        "",
        "## Synthetic Data Declaration",
        f"- **Human Curated tier**: Contains synthetic = {m['synthetic_data_declaration']['human_curated_tier']['contains_synthetic']}",
        f"  - {m['synthetic_data_declaration']['human_curated_tier']['description']}",
        f"- **Oracle Enhanced tier**: Contains synthetic = {m['synthetic_data_declaration']['oracle_enhanced_tier']['contains_synthetic']}",
        f"  - Generator: {m['synthetic_data_declaration']['oracle_enhanced_tier']['generator']}",
        f"  - Labeled: {m['synthetic_data_declaration']['oracle_enhanced_tier']['labeled']}",
        f"  - Separability: {m['synthetic_data_declaration']['oracle_enhanced_tier']['separability']}",
        "",
        "## Copyright Compliance",
        f"- **Source license**: {m['copyright_compliance']['source_material_license']}",
        f"- **US basis**: {m['copyright_compliance']['tdm_legal_basis']['us']}",
        f"- **EU basis**: {m['copyright_compliance']['tdm_legal_basis']['eu']}",
        f"- **UK**: {m['copyright_compliance']['tdm_legal_basis']['uk']}",
        "",
        "## Personal Data",
        f"- **Contains PII**: {m['personal_data']['contains_personal_data']}",
        f"- {m['personal_data']['dpla_assessment']}",
        "",
        "---",
        "",
        f"*{m['compliance_statement']}*",
    ])
    return "\n".join(lines)


def _manifest_to_markdown(m: dict[str, Any]) -> str:
    """Convert a structured manifest to a human-readable markdown document."""
    lines = [
        "# AB 2013 Data Provenance Compliance Manifest",
        "",
        f"**Generated**: {m['generated_at']}",
        f"**Order ID**: {m['order_id']}",
        f"**Manifest Version**: {m['manifest_version']}",
        "",
        "---",
        "",
        "## 1. Data Sources",
        f"- **Institution**: {m['data_sources']['institution']}",
        f"- **Collection ID**: {m['data_sources']['collection_id']}",
        f"- **API Endpoint**: {m['data_sources']['api_endpoint']}",
        f"- **Provider**: {m['data_sources']['provider']}",
        "",
        "## 2. Number of Data Points",
        f"- **Count**: {m['number_of_data_points']:,}",
        "",
        "## 3. Types of Data",
    ]
    for dt in m["data_types"]:
        lines.append(f"- {dt}")

    lines.extend([
        "",
        "## 4. Intellectual Property Status",
        f"- **Source images**: {m['intellectual_property_status']['source_images']}",
        f"- **Enrichment metadata**: {m['intellectual_property_status']['enrichment_metadata']}",
        f"- **Note**: {m['intellectual_property_status']['note']}",
        "",
        "## 5. Commercial Arrangement",
        f"- **License tier**: {m['commercial_arrangement']['license_tier']}",
        f"- **Payment**: {m['commercial_arrangement']['payment_amount']} "
        f"{m['commercial_arrangement']['currency']}",
        f"- **Method**: {m['commercial_arrangement']['payment_method']}",
        "",
        "## 6. Personally Identifiable Information (PII)",
        f"- **Contains PII**: {m['pii_declaration']['contains_pii']}",
        f"- {m['pii_declaration']['explanation']}",
        "",
        "## 7. Synthetic Data Declaration",
        f"- **Contains synthetic data**: {m['synthetic_data_declaration']['contains_synthetic_data']}",
        "",
        "### Synthetic Components",
    ])

    for sc in m["synthetic_data_declaration"]["synthetic_components"]:
        lines.append(f"- **{sc['field']}**: {sc['description']} (Generator: {sc['generator']})")

    lines.extend([
        "",
        "### Non-Synthetic Components",
    ])
    for ns in m["synthetic_data_declaration"]["non_synthetic_components"]:
        lines.append(f"- {ns}")

    lines.extend([
        "",
        "## 8. Collection Methodology",
        f"- **Method**: {m['collection_methodology']['method']}",
        f"- **Human curation**: {m['collection_methodology']['human_curation']}",
        "",
        "## 9. Date Range",
        f"- **Collection period**: {m['date_range']['collection_start']} to {m['date_range']['collection_end']}",
        f"- **Enrichment period**: {m['date_range']['enrichment_date']}",
        "",
        "## 10. Geographic Origin",
        f"- **Source**: {m['geographic_origin']['data_source_location']}",
        f"- **Processing**: {m['geographic_origin']['data_processing_location']}",
        f"- **Storage**: {m['geographic_origin']['data_storage_location']}",
        "",
        "## 11. Known Limitations",
    ])
    for lim in m["known_limitations"]:
        lines.append(f"- {lim}")

    lines.extend([
        "",
        "## 12. Contact Information",
        f"- **Provider**: {m['contact']['provider']}",
        f"- **Email**: {m['contact']['email']}",
        f"- **Website**: {m['contact']['website']}",
        f"- **API**: {m['contact']['api']}",
        f"- **DPO**: {m['contact']['data_protection_officer']}",
        "",
        "---",
        "",
        "*This manifest is auto-generated in compliance with California AB 2013 "
        "(Generative AI Training Data Transparency Act). For questions, contact "
        "data@iaeternum.ai.*",
    ])

    return "\n".join(lines)
