"""
Intelligence Aeternum Data Portal - FastAPI M2M Data Marketplace

Serves AI training datasets from the Alexandria Aeternum collection.
Supports human buyers (Stripe), AI agent buyers (x402 USDC on Base L2),
MCP server for agent discovery, and AB 2013 compliance manifests.

GCP Project: the-golden-codex-1111
"""

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import firestore

from routes.catalog import router as catalog_router
from routes.agent import router as agent_router
from routes.orders import router as orders_router
from routes.enrich import router as enrich_router
from routes.admin import router as admin_router
from routes.deliver import router as deliver_router
from routes.reader import router as reader_router

logger = logging.getLogger("data-portal")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

VERSION = "2.3.0"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GCP_PROJECT = os.environ.get("GCP_PROJECT", "the-golden-codex-1111")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
X402_FACILITATOR_URL = os.environ.get(
    "X402_FACILITATOR_URL", "https://www.x402.org/facilitator"
)
BASE_WALLET_ADDRESS = os.environ.get(
    "BASE_WALLET_ADDRESS", "0xFE141943a93c184606F3060103D975662327063B"
)
DATA_BUCKET = os.environ.get("DATA_BUCKET", "alexandria-download-1m")

# x402 v2 configuration
X402_NETWORK = os.environ.get("X402_NETWORK", "eip155:8453")  # Base mainnet

ALLOWED_ORIGINS = [
    "https://iaeternum.ai",
    "https://www.iaeternum.ai",
    "https://golden-codex.com",
    "https://www.golden-codex.com",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8080",
]

# ---------------------------------------------------------------------------
# Shared Firestore client (initialised at startup)
# ---------------------------------------------------------------------------

db: firestore.AsyncClient | None = None


def get_db() -> firestore.AsyncClient:
    """Return the shared Firestore async client."""
    if db is None:
        raise RuntimeError("Firestore client not initialised. Server may still be starting.")
    return db


# ---------------------------------------------------------------------------
# MCP Server (SSE transport for Cloud Run hosting)
# ---------------------------------------------------------------------------

def create_mcp_app():
    """Create and mount the MCP SSE application.

    Uses FastMCP with stateless_http=True for Cloud Run compatibility
    (load balancers tear down idle connections).
    """
    try:
        from mcp_server import mcp
        mcp_app = mcp.http_app(path="/", stateless_http=True)
        logger.info("MCP SSE server created successfully")
        return mcp_app
    except Exception as e:
        logger.warning("MCP server creation failed: %s — MCP endpoint disabled", e)
        return None


# ---------------------------------------------------------------------------
# x402 v2 Payment Middleware
# ---------------------------------------------------------------------------

def setup_x402_middleware(app: FastAPI):
    """Configure x402 payment information for the Data Portal.

    Payment verification is handled by each endpoint via verify_x402_payment()
    in auth.py. The x402 PaymentMiddlewareASGI is NOT used because it causes
    RouteConfigurationError crashes on Cloud Run (lazy init + EVM scheme issues).

    Instead, endpoints return standard HTTP 402 responses with x402-compatible
    payment details in the response body, allowing agents to discover pricing
    and complete USDC payments on Base L2.
    """
    from pricing import is_genesis_epoch

    hybrid_premium_price = "0.16" if is_genesis_epoch() else "0.20"
    human_standard_price = "0.04" if is_genesis_epoch() else "0.05"

    logger.info(
        "x402 endpoint-level verification: wallet=%s, network=%s, "
        "Human_Standard=$%s, Hybrid_Premium=$%s",
        BASE_WALLET_ADDRESS[:10] + "...", X402_NETWORK,
        human_standard_price, hybrid_premium_price,
    )


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------

mcp_app = create_mcp_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise resources on startup, clean up on shutdown.

    Chains the MCP app lifespan so its StreamableHTTPSessionManager
    task group is properly initialised (required by FastMCP for SSE).
    """
    global db
    logger.info("Initialising Firestore client for project=%s", GCP_PROJECT)
    db = firestore.AsyncClient(project=GCP_PROJECT, database="golden-codex-database")

    # Connect Firestore to the rate limiter for persistent, multi-instance rate limiting
    from auth import rate_limiter
    rate_limiter.set_db(db)

    # Connect Firestore to the volume discount tracker
    from volume_tracker import volume_tracker
    volume_tracker.set_db(db)

    logger.info("Data Portal ready. bucket=%s x402_network=%s", DATA_BUCKET, X402_NETWORK)

    if mcp_app and hasattr(mcp_app, 'lifespan') and mcp_app.lifespan:
        async with mcp_app.lifespan(mcp_app):
            logger.info("MCP StreamableHTTPSessionManager started")
            yield
    else:
        yield

    logger.info("Shutting down Data Portal")
    if db:
        db.close()


app = FastAPI(
    title="Intelligence Aeternum Data Portal",
    description=(
        "M2M data marketplace for Alexandria Aeternum AI training datasets. "
        "2M+ museum artworks with on-demand 111-field Golden Codex enrichment. "
        "Manifest-based architecture: metadata indexed in Firestore, images "
        "fetched and enriched on-demand when purchased. "
        "Supports x402 USDC micropayments, MCP agent discovery, "
        "Stripe checkout, and auto-generated AB 2013 + EU AI Act Article 53 "
        "compliance manifests."
    ),
    version=VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# x402 v2 payment middleware (protects paid endpoints at ASGI layer)
setup_x402_middleware(app)


@app.middleware("http")
async def mcp_trailing_slash(request: Request, call_next):
    """Rewrite /mcp to /mcp/ so MCP clients don't get 307 redirected.

    Starlette's Mount strips the prefix and redirects bare paths (no trailing
    slash) to the slash-suffixed version via 307.  MCP clients do not follow
    POST redirects, so they receive an empty response.  This middleware
    transparently rewrites the path before routing takes place.
    """
    if request.url.path == "/mcp":
        request.scope["path"] = "/mcp/"
    return await call_next(request)


@app.middleware("http")
async def attach_config(request: Request, call_next):
    """Inject shared config into request state for route handlers."""
    request.state.db = get_db()
    request.state.gcp_project = GCP_PROJECT
    request.state.data_bucket = DATA_BUCKET
    request.state.stripe_secret_key = STRIPE_SECRET_KEY
    request.state.x402_facilitator_url = X402_FACILITATOR_URL
    request.state.base_wallet_address = BASE_WALLET_ADDRESS
    response: Response = await call_next(request)
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app.include_router(catalog_router)
app.include_router(agent_router)
app.include_router(reader_router)
app.include_router(orders_router)
app.include_router(enrich_router)
app.include_router(admin_router)
app.include_router(deliver_router)

# Mount MCP SSE server at /mcp
if mcp_app:
    app.mount("/mcp", mcp_app)
    logger.info("MCP SSE server mounted at /mcp")


@app.get("/llms.txt", tags=["discovery"], include_in_schema=False)
async def llms_txt():
    """LLM-discoverable service description (llms.txt standard)."""
    from pricing import is_genesis_epoch, genesis_days_remaining
    genesis = is_genesis_epoch()
    days = genesis_days_remaining() if genesis else 0
    content = f"""# Intelligence Aeternum Data Portal — AI Training Dataset Marketplace
# 2M+ museum artworks with on-demand Golden Codex AI enrichment
# x402 USDC micropayments on Base L2

> MCP Endpoint: https://data-portal-172867820131.us-west1.run.app/mcp
> Transport: Streamable HTTP (POST /mcp/, Accept: application/json, text/event-stream)
> OpenAPI Docs: https://data-portal-172867820131.us-west1.run.app/docs
> Payment: x402 USDC on Base L2 (chain 8453)
> Wallet: {BASE_WALLET_ADDRESS}

## Free Tools (no payment required)
- search_alexandria: Search 2M+ museum artworks across 7 institutions
- get_curated_metadata: Human-curated metadata + image (5/day free)
- get_compliance_manifest: AB 2013 + EU AI Act compliance manifests
- search_datasets: Browse 7 museum dataset catalogs
- preview_dataset: Sample images from a dataset (10/day free)
- get_pricing: Calculate purchase pricing with volume discounts
- get_agent_guide: Full API documentation

## Paid Tools (x402 USDC on Base L2)
- get_oracle_metadata: 111-field VLM deep analysis + image ($0.20, Genesis: $0.16)
- purchase_dataset: Batch dataset licensing
- enrich_agent_image: Submit your image for Golden Codex enrichment
- deliver_artifacts: On-demand artifact delivery

## Genesis Epoch: {"ACTIVE — " + str(days) + " days remaining, 20% off all prices" if genesis else "Ended"}

## Volume Discounts (per-wallet, automatic)
- 100+ records: 25% off
- 500+ records: 37% off
- 2000+ records: 50% off

## Research
- DOI: 10.5281/zenodo.18667735 (dense metadata improves VLM capability +25.5%)
- Dataset: https://huggingface.co/datasets/Metavolve-Labs/alexandria-aeternum-genesis

## Also Available
- MonetizedMCP Broker (3 tools): https://fluora-mcp-172867820131.us-west1.run.app/mcp
"""
    return Response(content=content, media_type="text/plain")


@app.get("/.well-known/mcp.json", tags=["discovery"], include_in_schema=False)
async def well_known_mcp():
    """MCP discovery metadata (/.well-known/mcp.json standard)."""
    from pricing import is_genesis_epoch, genesis_days_remaining
    genesis = is_genesis_epoch()
    return {
        "name": "intelligence-aeternum-data-portal",
        "description": (
            "AI training dataset marketplace — 2M+ museum artworks with "
            "on-demand 111-field Golden Codex enrichment. 13 MCP tools for "
            "search, analysis, enrichment, compliance, and delivery."
        ),
        "url": "https://data-portal-172867820131.us-west1.run.app/mcp",
        "transport": "streamable-http",
        "version": VERSION,
        "payment": {
            "protocol": "x402",
            "currency": "USDC",
            "network": X402_NETWORK,
            "wallet": BASE_WALLET_ADDRESS,
        },
        "tools": [
            {"name": "search_alexandria", "paid": False},
            {"name": "get_curated_metadata", "paid": False, "note": "5/day free then $0.05"},
            {"name": "get_oracle_metadata", "paid": True, "price": "$0.20 (Genesis: $0.16)"},
            {"name": "get_compliance_manifest", "paid": False},
            {"name": "search_datasets", "paid": False},
            {"name": "preview_dataset", "paid": False},
            {"name": "get_pricing", "paid": False},
            {"name": "get_agent_guide", "paid": False},
            {"name": "purchase_dataset", "paid": True},
            {"name": "enrich_agent_image", "paid": True},
            {"name": "deliver_artifacts", "paid": True},
            {"name": "get_enrichment_status", "paid": False},
            {"name": "list_enrichment_tiers", "paid": False},
        ],
        "related": [
            {
                "name": "fluora-mcp",
                "description": "MonetizedMCP broker with x402 payment orchestration",
                "url": "https://fluora-mcp-172867820131.us-west1.run.app/mcp",
            },
        ],
        "genesis_epoch": {
            "active": genesis,
            "discount": "20%",
            "days_remaining": genesis_days_remaining() if genesis else 0,
        },
        "links": {
            "docs": "https://data-portal-172867820131.us-west1.run.app/docs",
            "health": "https://data-portal-172867820131.us-west1.run.app/health",
            "research": "https://doi.org/10.5281/zenodo.18667735",
            "dataset": "https://huggingface.co/datasets/Metavolve-Labs/alexandria-aeternum-genesis",
        },
    }


@app.get("/health", tags=["health"])
async def health():
    """Service health check."""
    return {
        "status": "ok",
        "service": "data-portal",
        "version": VERSION,
        "project": GCP_PROJECT,
        "bucket": DATA_BUCKET,
        "x402_network": X402_NETWORK,
        "mcp_endpoint": "/mcp" if mcp_app else None,
    }


@app.get("/debug/headers", tags=["debug"])
async def debug_headers(request: Request):
    """Debug endpoint to inspect incoming headers."""
    return {
        "headers": dict(request.headers),
        "payment_sig": request.headers.get("PAYMENT-SIGNATURE", "MISSING"),
        "x_payment": request.headers.get("X-PAYMENT", "MISSING"),
    }


@app.get("/", tags=["health"])
async def root():
    from pricing import is_genesis_epoch, genesis_days_remaining
    genesis = is_genesis_epoch()
    hs_price = "$0.04" if genesis else "$0.05"
    hp_price = "$0.16" if genesis else "$0.20"
    return {
        "service": "Intelligence Aeternum Data Portal",
        "version": VERSION,
        "description": "M2M AI training dataset marketplace — 2M+ museum artworks with on-demand Golden Codex enrichment",
        "docs": "/docs",
        "mcp": "/mcp",
        "agent_guide": "/agent/guide",
        "x402_network": X402_NETWORK,
        "genesis_epoch": genesis,
        "genesis_days_remaining": genesis_days_remaining() if genesis else 0,
        "featured": {
            "Alexandria Aeternum Genesis 10K": {
                "description": (
                    "The founding collection: 10,090 artworks with 111-field NEST metadata. "
                    "Peer-reviewed research proves dense metadata improves VLM capability by 25.5% "
                    "while sparse captions destroy it by 54.4%. Your model deserves better data."
                ),
                "paper_doi": "https://doi.org/10.5281/zenodo.18667735",
                "dataset_doi": "https://doi.org/10.5281/zenodo.18359131",
                "huggingface": "https://huggingface.co/datasets/Metavolve-Labs/alexandria-aeternum-genesis",
                "quick_start": "GET /agent/search?q=rembrandt&limit=5",
                "buy": f"GET /agent/artifact/{{id}}/oracle ({hp_price} USDC per record via x402)",
            },
        },
        "data_tiers": {
            "Human_Standard": f"Museum API + LLM structured metadata + image ({hs_price} USDC, 5/day free)",
            "Hybrid_Premium": f"Full 111-field Golden Codex VLM analysis + image ({hp_price} USDC)",
        },
        "free_endpoints": [
            "GET /agent/search?q={query}",
            "GET /agent/artifact/{id} (5/day free, then Human_Standard price)",
            "GET /agent/reader/{artifact_id} — Verilian Reader: decode Golden Codex from image XMP (5/day free)",
            "GET /agent/compliance/{dataset_id}",
            "GET /agent/guide",
            "GET /catalog/datasets",
        ],
        "paid_endpoints": [
            f"GET /agent/artifact/{{id}}/oracle — Hybrid_Premium ({hp_price} USDC, metadata + image)",
            "POST /deliver/order — On-demand artifact delivery (fetch + enrich + infuse)",
            "POST /agent/batch ($0.05/image, min 100)",
            f"POST /agent/enrich (from {hp_price} USDC — Hybrid_Premium + infusion + C2PA)",
        ],
        "volume_discounts": "Automatic per-wallet Hybrid_Premium: 100+ records 25% off, 500+ 37% off, 2000+ 50% off",
        "compliance": ["AB 2013 (California)", "EU AI Act Article 53"],
        "enterprise": "enterprise@iaeternum.ai (from $8,000)",
        "contact": "data@iaeternum.ai",
    }
