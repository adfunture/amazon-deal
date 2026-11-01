import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  experimental: {
    // Großhandelslisten werden über /api/backend an FastAPI durchgereicht
    proxyClientMaxBodySize: "110mb",
  },
  images: { unoptimized: true },
};

export default nextConfig;
