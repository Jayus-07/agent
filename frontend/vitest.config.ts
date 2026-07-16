import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "happy-dom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["src/types/**/*.ts", "src/lib/**/*.ts", "src/utils/**/*.ts"],
      exclude: ["**/*.test.{ts,tsx}", "**/*.d.ts"],
    },
  },
});