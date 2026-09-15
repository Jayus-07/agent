import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  // tsconfig 的 jsx: preserve（Next SSR 用）会让测试转换器不编译 JSX，
  // vitest 4 用 oxc 转换器，显式指定 automatic 运行时，测试才能直接 import tsx 组件
  oxc: { jsx: { runtime: "automatic" } },
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