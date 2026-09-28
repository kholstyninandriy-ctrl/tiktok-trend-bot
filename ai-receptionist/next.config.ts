import type { NextConfig } from "next";

/**
 * The widget is loaded inside an <iframe> on a third-party site, so the
 * /widget route must not send X-Frame-Options and must allow any frame-ancestor.
 * embed.js is fetched cross-origin by the client's page, so it needs CORS too.
 */
const nextConfig: NextConfig = {
  async headers() {
    return [
      {
        source: "/widget",
        headers: [
          { key: "Content-Security-Policy", value: "frame-ancestors *" },
        ],
      },
      {
        source: "/embed.js",
        headers: [
          { key: "Access-Control-Allow-Origin", value: "*" },
          { key: "Cache-Control", value: "public, max-age=300" },
        ],
      },
    ];
  },
};

export default nextConfig;
