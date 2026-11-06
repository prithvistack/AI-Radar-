// Site config. WORKER_URL is your deployed Cloudflare Worker (see README step 5).
window.RADAR_CONFIG = {
  // On localhost, use the Worker from `npx wrangler dev --port 8787`.
  WORKER_URL: location.hostname === "localhost"
    ? "http://localhost:8787"
    : "https://ai-radar-companion.p7721956.workers.dev",
  PAGE_SIZE: 30,
};
