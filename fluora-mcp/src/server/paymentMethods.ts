import { type PaymentMethodsResponse, PaymentMethods } from "monetizedmcp-sdk";
import { Config } from "../config/config.js";

export const paymentMethods: PaymentMethodsResponse[] = [
  {
    walletAddress: Config.SERVER_WALLET_ADDRESS,
    paymentMethod: PaymentMethods.USDC_BASE_MAINNET,
  },
];
