import { defineConfig } from "vite";
import preact from "@preact/preset-vite";
import { resolve } from "path";

export default defineConfig({
  plugins: [preact()],
  resolve: {
    alias: { "@": resolve(__dirname, "src") },
  },
  build: {
    outDir: resolve(__dirname, "../src/tessera/static/dist"),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        failover: resolve(__dirname, "src/pages/failover.tsx"),
        dhcp: resolve(__dirname, "src/pages/dhcp.tsx"),
        protection: resolve(__dirname, "src/pages/protection.tsx"),
        servers: resolve(__dirname, "src/pages/servers.tsx"),
        voters: resolve(__dirname, "src/pages/voters.tsx"),
      },
      output: {
        entryFileNames: "[name].[hash].js",
        chunkFileNames: "chunks/[name].[hash].js",
        assetFileNames: "assets/[name].[hash][extname]",
      },
    },
    cssCodeSplit: true,
    sourcemap: false,
    minify: "esbuild",
    target: "es2020",
  },
});
