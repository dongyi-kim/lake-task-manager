// Local settings responses are never memoized; secrets are fetched only on explicit actions.
import { api } from "./api.js";

function change(path, method, body) {
  return api.raw("/api/mcp/" + path, {
    method, cache: "no-store", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-LTM-Local": "1" },
    body: JSON.stringify(body),
  });
}

export const localMcpApi = {
  settings: () => api.raw("/api/mcp/settings", { cache: "no-store" }),
  configure: (enabled) => change("settings", "PUT", { enabled }),
  credentials: (format, profile = "all") => change("credentials", "POST", { format, profile }),
  rotate: () => change("rotate", "POST", {}),
};
