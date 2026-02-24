# Intelligence Aeternum MCP

**AI training dataset marketplace exposed as MCP servers.** 2M+ museum artworks across 7 world-class institutions with on-demand 111-field Golden Codex AI enrichment. x402 USDC micropayments on Base L2.

[![Research](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.18667735-blue)](https://doi.org/10.5281/zenodo.18667735)
[![Dataset](https://img.shields.io/badge/HuggingFace-Alexandria%20Aeternum-yellow)](https://huggingface.co/datasets/Metavolve-Labs/alexandria-aeternum-genesis)
[![License](https://img.shields.io/badge/license-proprietary-red)](#license)

---

## Quick Start

Connect to the MCP endpoint with any MCP-compatible client:

```json
{
  "mcpServers": {
    "intelligence-aeternum": {
      "url": "https://data-portal-172867820131.us-west1.run.app/mcp",
      "transport": "streamable-http"
    }
  }
}
```

Or use the MonetizedMCP broker (x402 payment orchestration):

```json
{
  "mcpServers": {
    "fluora-mcp": {
      "url": "https://fluora-mcp-172867820131.us-west1.run.app/mcp",
      "transport": "streamable-http"
    }
  }
}
```

---

## Architecture

```
AI Agent (Claude, GPT, etc.)
    |
    +---> Fluora MCP (MonetizedMCP broker)         [TypeScript/Express]
    |     3 tools: price-listing, payment-methods, make-purchase
    |     Orchestrates x402 USDC payments via MonetizedMCP SDK
    |     Proxies to Data Portal with payment proof
    |
    +---> Data Portal MCP (FastMCP content server)  [Python/FastAPI]
          13 tools: search, metadata, oracle, enrichment, compliance...
          Serves 2M+ artworks from Firestore manifest
          On-demand VLM enrichment via Gemini
```

---

## Services

### Data Portal (13 MCP Tools)

| Tool | Paid? | Description |
|------|-------|-------------|
| `search_alexandria` | Free | Search 2M+ museum artworks across 7 institutions |
| `get_curated_metadata` | Free (5/day) | Human_Standard metadata + image (500-1,200 tokens) |
| `get_oracle_metadata` | $0.16 USDC | Hybrid_Premium 111-field VLM deep analysis + image |
| `get_compliance_manifest` | Free | AB 2013 + EU AI Act Article 53 compliance manifests |
| `search_datasets` | Free | Browse 7 museum dataset catalogs |
| `preview_dataset` | Free | Sample images from a dataset |
| `get_pricing` | Free | Calculate pricing with volume discounts |
| `get_agent_guide` | Free | Complete API workflow documentation |
| `list_enrichment_tiers` | Free | Available enrichment tiers and pricing |
| `get_enrichment_status` | Free | Poll enrichment job status |
| `purchase_dataset` | Paid | Initiate batch dataset purchase |
| `enrich_agent_image` | From $0.16 | Submit YOUR image for Golden Codex enrichment |
| `deliver_artifacts` | From $0.04 | On-demand artifact delivery |

### Fluora MCP (MonetizedMCP Broker)

| Tool | Description |
|------|-------------|
| `price-listing` | Browse 7 purchasable data products with pricing |
| `payment-methods` | Get accepted payment methods (USDC on Base L2) |
| `make-purchase` | Purchase data with x402 USDC micropayment |

---

## Pipeline Tools (Coming to MCP)

| Tool | Description | Price |
|------|-------------|-------|
| **SD 3.5 Large + T5-XXL** | Image generation on NVIDIA L4 GPU with LoRA support | TBD |
| **ESRGAN x4 Upscaler** | Real-ESRGAN super-resolution (1024px to 4096px in ~1.15s) | $0.10 |
| **Nova (Metadata Creation)** | 111-field Golden Codex VLM analysis via Gemini | $0.20 |
| **Atlas (Metadata Infusion)** | XMP/IPTC/C2PA embedding + SHA-256 Soulmark + hash registry | $0.10 |
| **Aegis (Verification)** | "Shazam for Art" — perceptual hash provenance lookup | Free |
| **Archivus (Arweave Storage)** | Permanent 200+ year storage — pay USDC, no AR needed | TBD |
| **Mintra (NFT Minting)** | Polygon NFT with full Golden Codex metadata on-chain | TBD |

---

## Museums

| Institution | Artworks | License |
|-------------|----------|---------|
| Metropolitan Museum of Art | 375,000 | CC0 |
| Rijksmuseum | 709,000 | CC0 |
| Smithsonian Institution | 185,000 | CC0 |
| National Gallery of Art | 130,000 | CC0 |
| Art Institute of Chicago | 120,000 | CC0 |
| Cleveland Museum of Art | 61,000 | CC0 |
| Paris Collections (Louvre, Orsay, Rodin) | 45,000 | CC0 |

All source images are CC0/public domain. Enrichment metadata is commercially licensed by Metavolve Labs.

---

## Payment

Paid tools use **x402 USDC micropayments on Base L2**. No API keys, no subscriptions — AI agents pay autonomously.

1. Call a paid tool without payment
2. Receive HTTP 402 with x402 payment envelope
3. Execute USDC transfer on Base L2
4. Re-call with `X-PAYMENT` header containing transaction proof
5. Receive data

**Wallet:** `0xFE141943a93c184606F3060103D975662327063B`

### Genesis Epoch (Launch Pricing)

**20% off all prices for 90 days** (started Feb 23, 2026).

Volume discounts auto-apply per wallet:
- 100+ records: 25% off
- 500+ records: 37% off
- 2000+ records: 50% off

---

## Discovery

| Endpoint | URL |
|----------|-----|
| Data Portal MCP | `https://data-portal-172867820131.us-west1.run.app/mcp` |
| Fluora MCP | `https://fluora-mcp-172867820131.us-west1.run.app/mcp` |
| OpenAPI Docs | `https://data-portal-172867820131.us-west1.run.app/docs` |
| llms.txt (Data Portal) | `https://data-portal-172867820131.us-west1.run.app/llms.txt` |
| llms.txt (Fluora) | `https://fluora-mcp-172867820131.us-west1.run.app/llms.txt` |
| .well-known/mcp.json | `https://data-portal-172867820131.us-west1.run.app/.well-known/mcp.json` |
| Schema (Fluora) | `https://fluora-mcp-172867820131.us-west1.run.app/mcp/schema` |
| Health (Data Portal) | `https://data-portal-172867820131.us-west1.run.app/health` |
| Health (Fluora) | `https://fluora-mcp-172867820131.us-west1.run.app/health` |

---

## Research

Our peer-reviewed paper *The Density Imperative* demonstrates that dense metadata significantly impacts VLM capability:

- **+25.5%** improvement with 111-field Golden Codex enrichment
- **-54.4%** degradation with sparse 10-20 word captions
- **+160%** semantic coverage vs raw captions

**Paper:** [DOI: 10.5281/zenodo.18667735](https://doi.org/10.5281/zenodo.18667735)

---

## Directory Structure

```
intelligence-aeternum-mcp/
+-- fluora-mcp/              # MonetizedMCP broker (TypeScript/Express)
|   +-- src/
|   |   +-- main.ts          # Entrypoint
|   |   +-- server/
|   |   |   +-- server.ts    # MCP server + HTTP endpoints
|   |   |   +-- purchasableItems.ts  # 7 product catalog
|   |   |   +-- paymentMethods.ts    # USDC on Base L2
|   |   +-- config/
|   |       +-- config.ts    # Environment config
|   +-- Dockerfile
|   +-- package.json
|   +-- deploy.sh
|
+-- data-portal/             # FastMCP content server (Python/FastAPI)
|   +-- main.py              # FastAPI app + MCP mount
|   +-- mcp_server.py        # 13 MCP tool definitions
|   +-- auth.py              # x402 payment verification + rate limiting
|   +-- pricing.py           # Pricing tiers + Genesis Epoch
|   +-- compliance.py        # AB 2013 + EU AI Act manifests
|   +-- volume_tracker.py    # Per-wallet volume discounts
|   +-- image_fetcher.py     # Museum API integration
|   +-- routes/
|   |   +-- agent.py         # Agent API endpoints
|   |   +-- reader.py        # Verilian Reader (decode XMP metadata)
|   |   +-- catalog.py       # Dataset catalog
|   |   +-- orders.py        # Stripe + x402 orders
|   |   +-- enrich.py        # Enrichment-as-a-Service
|   |   +-- deliver.py       # On-demand delivery
|   |   +-- admin.py         # Admin utilities
|   +-- Dockerfile
|   +-- requirements.txt
|   +-- deploy.sh
|
+-- glama.json               # Glama.ai MCP directory manifest
+-- README.md                # This file
```

---

## Compliance

Every purchase auto-generates provenance manifests at no additional cost:

- **AB 2013** (California AI Training Data Transparency Act)
- **EU AI Act Article 53** (Training data documentation requirements)

---

## Enterprise

Full dataset access with compliance manifests starting at $8,000.

Contact: **enterprise@iaeternum.ai**

---

## License

Source code in this repository is provided for reference and transparency. The enrichment metadata, Golden Codex schema, and pipeline services are proprietary to Metavolve Labs, Inc. Source images from museums are CC0/public domain.

---

## Links

- **Website:** [iaeternum.ai](https://iaeternum.ai)
- **Golden Codex:** [golden-codex.com](https://golden-codex.com)
- **HuggingFace:** [Metavolve-Labs/alexandria-aeternum-genesis](https://huggingface.co/datasets/Metavolve-Labs/alexandria-aeternum-genesis)
- **Research:** [DOI: 10.5281/zenodo.18667735](https://doi.org/10.5281/zenodo.18667735)

---

*Metavolve Labs, Inc. | San Francisco, California*
*"Synthetic Data is not the problem. Synthetic Garbage is."*
