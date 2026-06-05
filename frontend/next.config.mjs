import path from "node:path";
import { fileURLToPath } from "node:url";
import { config as loadEnv } from "dotenv";

// Load the shared project-root env files so a single root `.env` configures the
// frontend too. dotenv does not override variables already set in the real
// environment (e.g. the build ARG provided by Docker Compose), and silently
// no-ops when the files are absent (as in the Docker build context).
const rootDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
loadEnv({ path: path.join(rootDir, ".env") });
loadEnv({ path: path.join(rootDir, ".env.local") });

// Where the Next server forwards /api/* requests (server-side, NOT a
// NEXT_PUBLIC_ var — the browser never sees it). Default suits local dev; in
// Docker Compose it's the backend service name. Read when next.config loads:
// at startup for `next dev`, at build time for the standalone/production server.
const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN || "http://localhost:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // Don't gzip-buffer the proxied NDJSON stream — keep events trickling.
  compress: false,
  // Same-origin reverse proxy: the browser calls relative /api/*, Next forwards
  // it to the backend. No baked-in IP, no CORS — works from any device.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND_ORIGIN}/api/:path*` },
    ];
  },
};

export default nextConfig;
