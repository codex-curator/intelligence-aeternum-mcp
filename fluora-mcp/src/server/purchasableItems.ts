import { type PurchasableItem, PaymentMethods } from "monetizedmcp-sdk";

export const purchasableItems: PurchasableItem[] = [
  {
    id: "search-alexandria",
    name: "Search Alexandria Aeternum",
    description:
      "Search 2M+ museum artworks across 7 world-class institutions (Genesis Epoch). " +
      "Returns artifact IDs, titles, artists, dates, and classification. " +
      "Free — no payment required.",
    price: {
      amount: 0,
      paymentMethod: PaymentMethods.USDC_BASE_MAINNET,
    },
    params: {
      query: "Example: impressionist landscape",
      museum: "Optional: met, nga, chicago, cleveland, rijksmuseum, smithsonian",
      limit: "Optional: max results (default 20, max 100)",
    },
  },
  {
    id: "curated-metadata",
    name: "Human_Standard Metadata + Image",
    description:
      "Human_Standard package: full human-curated metadata + image download for a museum artifact. " +
      "500-1,200 tokens of 100% human-sourced data from Museum API + Wikipedia + Wikidata + Getty ULAN. " +
      "Zero synthetic content. 5 free per day, then $0.05 USDC each (Genesis Epoch: $0.04).",
    price: {
      amount: 0.05,
      paymentMethod: PaymentMethods.USDC_BASE_MAINNET,
    },
    params: {
      artifact_id: "Example: met_10049",
    },
  },
  {
    id: "oracle-metadata",
    name: "Hybrid_Premium VLM Metadata + Image",
    description:
      "Hybrid_Premium package: deep visual analysis powered by Gemini VLM + image download. " +
      "2,000-6,000 tokens including color palette, composition, lighting, " +
      "style analysis, emotional journey, deep symbolism, and archetypal analysis. " +
      "$0.20 USDC (Genesis Epoch: $0.16). " +
      "Volume discounts: 100+ 25% off, 500+ 37% off, 2000+ 50% off. " +
      "Research (DOI: 10.5281/zenodo.18667735) shows dense metadata improves " +
      "VLM visual perception by +25.5%.",
    price: {
      amount: 0.20,
      paymentMethod: PaymentMethods.USDC_BASE_MAINNET,
    },
    params: {
      artifact_id: "Example: met_10049",
    },
  },
  {
    id: "batch-download",
    name: "Batch Artifact Download",
    description:
      "Bulk download of museum artwork metadata + images. " +
      "Minimum 100 images at $0.05/image USDC. " +
      "Includes compliance manifests (AB 2013 + EU AI Act Article 53).",
    price: {
      amount: 5.0,
      paymentMethod: PaymentMethods.USDC_BASE_MAINNET,
    },
    params: {
      image_ids: "Array of artifact IDs (min 100)",
      dataset_id: "Or specify a dataset: met-museum, rijksmuseum, nga, etc.",
    },
  },
  {
    id: "compliance-manifest",
    name: "Compliance Manifest",
    description:
      "Auto-generated AB 2013 (California) and EU AI Act Article 53 provenance manifests. " +
      "Ready for regulatory submission. Free — no payment required.",
    price: {
      amount: 0,
      paymentMethod: PaymentMethods.USDC_BASE_MAINNET,
    },
    params: {
      dataset_id: "Example: met-museum, rijksmuseum, nga",
      regulation: "Optional: ab2013, eu_ai_act, or all (default)",
    },
  },
  {
    id: "enrich-certified",
    name: "Golden Codex Enrichment (Hybrid_Premium + Infuse)",
    description:
      "Submit your own image for Golden Codex enrichment. " +
      "Hybrid_Premium 111-field reading + XMP metadata infusion + GCX hash registration. " +
      "$0.30 USDC (Genesis Epoch: $0.24). The densest art metadata in the industry.",
    price: {
      amount: 0.30,
      paymentMethod: PaymentMethods.USDC_BASE_MAINNET,
    },
    params: {
      image_url: "Public URL of the image to enrich",
      callback_url: "Optional: webhook URL for completion notification",
    },
  },
  {
    id: "enrich-full-pipeline",
    name: "Golden Codex Enrichment (Full Certified Pipeline)",
    description:
      "Full Certified Pipeline: Hybrid_Premium reading + XMP infusion + " +
      "C2PA Content Credentials + hash registry + Arweave permanent storage + NFT minting. " +
      "$0.50 USDC (Genesis Epoch: $0.40). The ultimate provenance package.",
    price: {
      amount: 0.50,
      paymentMethod: PaymentMethods.USDC_BASE_MAINNET,
    },
    params: {
      image_url: "Public URL of the image to enrich",
      callback_url: "Optional: webhook URL for completion notification",
    },
  },
];

export function filterPurchasableItems(
  searchQuery: string,
): PurchasableItem[] {
  const q = searchQuery.toLowerCase();
  return purchasableItems.filter(
    (item) =>
      item.name.toLowerCase().includes(q) ||
      (item.description ?? "").toLowerCase().includes(q),
  );
}
