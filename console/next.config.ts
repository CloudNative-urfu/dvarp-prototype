import path from "node:path";
import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // the console is its own package inside the polygon repo
  turbopack: { root: path.resolve(".") },
};

export default config;
