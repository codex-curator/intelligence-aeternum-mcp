import {
  MonetizedMCPServer,
  type MakePurchaseRequest,
  type MakePurchaseResponse,
  type PaymentMethodsResponse,
  type PriceListingRequest,
  type PriceListingResponse,
  PaymentsTools,
} from "monetizedmcp-sdk";
import {
  purchasableItems,
  filterPurchasableItems,
} from "./purchasableItems.js";
import { paymentMethods } from "./paymentMethods.js";
import { v4 as uuidv4 } from "uuid";
import { Config } from "../config/config.js";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { isInitializeRequest } from "@modelcontextprotocol/sdk/types.js";
import { randomUUID } from "node:crypto";
import { z } from "zod";
import { PaymentMethods } from "monetizedmcp-sdk";
import express from "express";

const DATA_PORTAL = Config.DATA_PORTAL_URL;

/** Map purchasable item IDs to data portal API calls. */
async function deliverService(
  itemId: string,
  params: Record<string, string>,
  paymentHeader?: string,
): Promise<string> {
  let url: string;
  let method = "GET";
  let body: string | undefined;

  switch (itemId) {
    case "search-alexandria":
      url = `${DATA_PORTAL}/agent/search?q=${encodeURIComponent(params["query"] ?? "")}&limit=${params["limit"] ?? "20"}`;
      if (params["museum"]) url += `&museum=${encodeURIComponent(params["museum"])}`;
      break;

    case "curated-metadata":
      url = `${DATA_PORTAL}/agent/artifact/${encodeURIComponent(params["artifact_id"] ?? "")}`;
      break;

    case "oracle-metadata":
      url = `${DATA_PORTAL}/agent/artifact/${encodeURIComponent(params["artifact_id"] ?? "")}/oracle`;
      break;

    case "image-download":
      url = `${DATA_PORTAL}/agent/image/${encodeURIComponent(params["image_id"] ?? "")}`;
      break;

    case "batch-download": {
      url = `${DATA_PORTAL}/agent/batch`;
      method = "POST";
      body = JSON.stringify({
        image_ids: params["image_ids"] ? JSON.parse(params["image_ids"]) : [],
        dataset_id: params["dataset_id"] ?? "",
      });
      break;
    }

    case "compliance-manifest":
      url = `${DATA_PORTAL}/agent/compliance/${encodeURIComponent(params["dataset_id"] ?? "")}?regulation=${params["regulation"] ?? "all"}`;
      break;

    case "enrich-certified":
    case "enrich-full-pipeline": {
      const tier = itemId === "enrich-certified" ? "certified" : "full_pipeline";
      url = `${DATA_PORTAL}/enrich`;
      method = "POST";
      body = JSON.stringify({
        image_url: params["image_url"] ?? "",
        tier,
        callback_url: params["callback_url"] ?? "",
      });
      break;
    }

    default:
      throw new Error(`Unknown purchasable item: ${itemId}. Use 'price-listing' tool to see available items.`);
  }

  // Forward payment headers so the data portal can verify the settled payment
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (paymentHeader) {
    headers["X-PAYMENT"] = paymentHeader;
  }

  const res = await fetch(url, {
    method,
    headers,
    ...(body ? { body } : {}),
  });

  return await res.text();
}

/** Create a fresh McpServer with tools registered, bound to our handler. */
function createMcpServer(handler: Server): McpServer {
  const server = new McpServer({
    name: "intelligence-aeternum",
    version: "1.0.0",
  });

  server.tool(
    "price-listing",
    { searchQuery: z.string().optional() },
    async ({ searchQuery }) => {
      const listing = await handler.priceListing({ searchQuery });
      return { content: [{ type: "text" as const, text: JSON.stringify(listing) }] };
    },
  );

  server.tool("payment-methods", {}, async () => {
    const methods = await handler.paymentMethods();
    return { content: [{ type: "text" as const, text: JSON.stringify(methods) }] };
  });

  server.tool(
    "make-purchase",
    {
      itemId: z.string(),
      params: z.record(z.string(), z.any()),
      signedTransaction: z.string(),
      paymentMethod: z.nativeEnum(PaymentMethods),
    },
    async ({ itemId, params, signedTransaction, paymentMethod }) => {
      const purchase = await handler.makePurchase({
        itemId,
        params,
        signedTransaction,
        paymentMethod,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(purchase) }] };
    },
  );

  return server;
}

export class Server extends MonetizedMCPServer {
  async priceListing(
    priceListingRequest: PriceListingRequest,
  ): Promise<PriceListingResponse> {
    const items = priceListingRequest.searchQuery
      ? filterPurchasableItems(priceListingRequest.searchQuery)
      : purchasableItems;
    return { items };
  }

  async paymentMethods(): Promise<PaymentMethodsResponse[]> {
    return paymentMethods;
  }

  async makePurchase(
    purchaseRequest: MakePurchaseRequest,
  ): Promise<MakePurchaseResponse> {
    console.log("makePurchase", purchaseRequest);

    if (!Config.SERVER_WALLET_ADDRESS) {
      throw new Error("SERVER_WALLET_ADDRESS environment variable is not set");
    }

    const item = purchasableItems.find(
      (i) =>
        i.id === purchaseRequest.itemId &&
        i.price.paymentMethod === purchaseRequest.paymentMethod,
    );

    if (!item) {
      return {
        purchasableItemId: purchaseRequest.itemId,
        makePurchaseRequest: purchaseRequest,
        orderId: uuidv4(),
        toolResult: "Item not found. Check the item ID and payment method.",
      };
    }

    // Free items skip payment verification
    if (item.price.amount > 0) {
      const paymentsTools = new PaymentsTools();
      console.log(`Verifying payment: ${item.name} ($${item.price.amount} USDC)`);

      const payment = await paymentsTools.verifyAndSettlePayment(
        item.price.amount,
        Config.SERVER_WALLET_ADDRESS as `0x${string}`,
        {
          facilitatorUrl: "https://x402.org/facilitator",
          paymentHeader: purchaseRequest.signedTransaction,
          resource: `https://${DATA_PORTAL.replace(/^https?:\/\//, "")}/fluora/${item.id}` as `${string}://${string}`,
          paymentMethod: purchaseRequest.paymentMethod,
        },
      );

      console.log("Payment result:", payment);

      if (!payment.success) {
        return {
          purchasableItemId: purchaseRequest.itemId,
          makePurchaseRequest: purchaseRequest,
          orderId: uuidv4(),
          toolResult: `Payment failed: ${payment.message || "Unknown error"}`,
        };
      }
    }

    // Deliver the service by proxying to our data portal
    console.log(`Delivering service: ${item.name}`);
    const params = (purchaseRequest as Record<string, unknown>)["params"] as
      | Record<string, string>
      | undefined;
    const result = await deliverService(item.id, params ?? {}, purchaseRequest.signedTransaction);

    return {
      purchasableItemId: purchaseRequest.itemId,
      makePurchaseRequest: purchaseRequest,
      orderId: uuidv4(),
      toolResult: result,
    };
  }

  constructor() {
    super();
    // Override: run our own HTTP server with per-session McpServer instances
    this.startServer();
  }

  private startServer(): void {
    console.log("Starting Intelligence Aeternum MonetizedMCP server");
    const app = express();
    app.use(express.json());

    const transports: Record<string, StreamableHTTPServerTransport> = {};

    app.post("/mcp", async (req, res) => {
      const sessionId = req.headers["mcp-session-id"] as string | undefined;

      let transport: StreamableHTTPServerTransport;

      if (sessionId && transports[sessionId]) {
        transport = transports[sessionId];
      } else if (!sessionId && isInitializeRequest(req.body)) {
        // Create a NEW McpServer instance per session (fixes "Already connected" error)
        const mcpServer = createMcpServer(this);

        transport = new StreamableHTTPServerTransport({
          sessionIdGenerator: () => randomUUID(),
          onsessioninitialized: (sid: string) => {
            transports[sid] = transport;
          },
        });

        transport.onclose = () => {
          if (transport.sessionId) {
            delete transports[transport.sessionId];
          }
        };

        await mcpServer.connect(transport);
      } else {
        res.status(400).json({
          jsonrpc: "2.0",
          error: { code: -32000, message: "Bad Request: No valid session ID provided" },
          id: null,
        });
        return;
      }

      await transport.handleRequest(req, res, req.body);
    });

    const handleSessionRequest = async (
      req: express.Request,
      res: express.Response,
    ): Promise<void> => {
      const sessionId = req.headers["mcp-session-id"] as string | undefined;
      if (!sessionId || !transports[sessionId]) {
        res.status(400).send("Invalid or missing session ID");
        return;
      }
      await transports[sessionId].handleRequest(req, res);
    };

    app.get("/mcp", handleSessionRequest);
    app.delete("/mcp", handleSessionRequest);

    // ---- Discovery Endpoints (llms.txt + .well-known/mcp.json) ----

    app.get("/llms.txt", (_req, res) => {
      res.type("text/plain").send(
        `# Intelligence Aeternum — MonetizedMCP Data Marketplace
# 2M+ museum artworks with on-demand Golden Codex AI enrichment
# x402 USDC micropayments on Base L2

> MCP Endpoint: https://fluora-mcp-172867820131.us-west1.run.app/mcp
> Transport: Streamable HTTP (POST /mcp, Accept: application/json, text/event-stream)
> Schema: https://fluora-mcp-172867820131.us-west1.run.app/mcp/schema
> Payment: x402 USDC on Base L2 (chain 8453)
> Wallet: ${Config.SERVER_WALLET_ADDRESS}

## Tools
- price-listing: Browse 7 purchasable data products (free)
- payment-methods: Get accepted payment methods — USDC on Base L2 (free)
- make-purchase: Buy data with x402 USDC micropayment

## Products
- search-alexandria: Search 2M+ museum artworks across 7 institutions (FREE)
- curated-metadata: Human-curated metadata + image ($0.05, 5/day free)
- oracle-metadata: 111-field VLM deep analysis + image ($0.20)
- batch-download: Bulk download 100+ artworks ($5.00)
- compliance-manifest: AB 2013 + EU AI Act manifests (FREE)
- enrich-certified: Submit your image for Golden Codex enrichment ($0.30)
- enrich-full-pipeline: Full certified pipeline with C2PA + NFT ($0.50)

## Genesis Epoch: 20% off all prices for 90 days

## Research
- DOI: 10.5281/zenodo.18667735 (dense metadata improves VLM capability +25.5%)
- Dataset: https://huggingface.co/datasets/Metavolve-Labs/alexandria-aeternum-genesis

## Also Available
- Data Portal MCP (FastMCP, 13 tools): https://data-portal-172867820131.us-west1.run.app/mcp
- OpenAPI Docs: https://data-portal-172867820131.us-west1.run.app/docs
`
      );
    });

    app.get("/.well-known/mcp.json", (_req, res) => {
      res.json({
        name: "intelligence-aeternum",
        description:
          "AI training dataset marketplace — 2M+ museum artworks with on-demand 111-field Golden Codex enrichment. x402 USDC micropayments on Base L2.",
        url: "https://fluora-mcp-172867820131.us-west1.run.app/mcp",
        transport: "streamable-http",
        version: "1.0.0",
        payment: {
          protocol: "x402",
          currency: "USDC",
          network: "eip155:8453",
          wallet: Config.SERVER_WALLET_ADDRESS,
        },
        tools: [
          { name: "price-listing", description: "Browse purchasable data products" },
          { name: "payment-methods", description: "Get accepted payment methods" },
          { name: "make-purchase", description: "Purchase data with x402 USDC" },
        ],
        related: [
          {
            name: "data-portal-mcp",
            description: "FastMCP server with 13 tools for direct API access",
            url: "https://data-portal-172867820131.us-west1.run.app/mcp",
          },
        ],
        genesis_epoch: { active: true, discount: "20%", expires: "2026-05-23" },
        links: {
          schema: "https://fluora-mcp-172867820131.us-west1.run.app/mcp/schema",
          health: "https://fluora-mcp-172867820131.us-west1.run.app/health",
          docs: "https://data-portal-172867820131.us-west1.run.app/docs",
          research: "https://doi.org/10.5281/zenodo.18667735",
          dataset: "https://huggingface.co/datasets/Metavolve-Labs/alexandria-aeternum-genesis",
        },
      });
    });

    // Root endpoint (service discovery)
    app.get("/", (_req, res) => {
      res.json({
        service: "Intelligence Aeternum MonetizedMCP",
        version: "1.0.0",
        mcp_endpoint: "/mcp",
        health: "/health",
        schema: "/mcp/schema",
        payment: "x402 USDC on Base L2",
        wallet: Config.SERVER_WALLET_ADDRESS,
        dataPortal: DATA_PORTAL,
        tools: ["price-listing", "payment-methods", "make-purchase"],
        purchasable_items: purchasableItems.length,
      });
    });

    // MCP schema endpoint (tool/item discovery without establishing a session)
    app.get("/mcp/schema", (_req, res) => {
      res.json({
        name: "intelligence-aeternum",
        version: "1.0.0",
        tools: [
          { name: "price-listing", description: "List purchasable items with optional search filter" },
          { name: "payment-methods", description: "Get accepted payment methods (USDC on Base L2)" },
          { name: "make-purchase", description: "Purchase an item with x402 USDC payment" },
        ],
        purchasableItems: purchasableItems.map((i) => ({
          id: i.id,
          name: i.name,
          description: i.description,
          price: i.price.amount,
          currency: "USDC",
          params: i.params,
        })),
      });
    });

    // Health + activity monitoring
    app.get("/health", (_req, res) => {
      res.json({
        status: "healthy",
        service: "fluora-mcp",
        version: "1.0.0",
        activeSessions: Object.keys(transports).length,
        uptime: process.uptime(),
        dataPortal: DATA_PORTAL,
      });
    });

    const port = process.env["PORT"] || 8080;
    app.listen(port, () => {
      console.log(`Intelligence Aeternum MCP server listening on port ${port}`);
    });
  }
}
