// Standalone Cloudflare Worker entry for the Context Pill MCP server (optional; contextpill.com/mcp already serves it).
// Deploy: npx wrangler deploy mcp/worker.mjs --name context-pill-mcp --compatibility-date 2025-01-01
import { handleMcp } from "./server.mjs";

export default {
  async fetch(request) {
    const { pathname } = new URL(request.url);
    if (pathname === "/mcp") return handleMcp(request);
    return new Response("Context Pill MCP server. Endpoint: POST /mcp", { status: 404, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  },
};
