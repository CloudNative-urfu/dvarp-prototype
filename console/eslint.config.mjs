import next from "eslint-config-next";

const config = [...next, { ignores: [".next/**", "node_modules/**", ".data/**", "next-env.d.ts"] }];

export default config;
