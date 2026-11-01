import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("RootLayout QueryClient 边界", () => {
  it("Sidebar 必须位于真实 QueryClientProvider 内，才能取得根布局的 client", () => {
    const source = readFileSync(resolve(process.cwd(), "src/app/layout.tsx"), "utf8");
    const providerStart = source.indexOf("<QueryClientProvider");
    const sidebarStart = source.indexOf("<Sidebar");
    const providerEnd = source.indexOf("</QueryClientProvider>");

    expect(providerStart).toBeGreaterThanOrEqual(0);
    expect(sidebarStart).toBeGreaterThanOrEqual(0);
    expect(providerEnd).toBeGreaterThan(providerStart);
    expect(sidebarStart).toBeGreaterThan(providerStart);
    expect(sidebarStart).toBeLessThan(providerEnd);
  });

  it("工作台判定为段级匹配（/cs 或 /cs/ 子路径），防止未来同前缀路由误入工作台布局", () => {
    const source = readFileSync(resolve(process.cwd(), "src/app/layout.tsx"), "utf8");
    expect(source).toContain("pathname === '/cs' || pathname.startsWith('/cs/')");
    expect(source).not.toContain("pathname.startsWith('/cs')");
  });
});
