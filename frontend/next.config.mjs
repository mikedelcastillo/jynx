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

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
};

export default nextConfig;
