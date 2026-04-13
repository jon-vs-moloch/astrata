export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/health") {
      return json({
        ok: true,
        service: "astrata-distribution",
        environment: env.ENVIRONMENT || "development",
      });
    }

    if (url.pathname === "/distribution") {
      return json({
        status: "live",
        message:
          "Astrata distribution is live on Cloudflare Pages + Workers, with release artifacts stored in R2 and served through the distribution worker.",
        public_download_site: env.PUBLIC_DOWNLOAD_SITE || null,
        public_releases_host: env.PUBLIC_RELEASES_HOST || null,
        channels: channelCatalog(url),
      });
    }

    if (url.pathname.startsWith("/downloads/")) {
      const parts = url.pathname.split("/").filter(Boolean);
      if (parts.length === 4 && parts[0] === "downloads" && parts[2] === "macos" && parts[3] === "Astrata-macos-app.zip") {
        const channel = parts[1];
        const object = await env.RELEASE_ARTIFACTS.get(`${channel}/macos/Astrata-macos-app.zip`);
        if (!object) return json({ error: "not_found", channel }, 404);
        const headers = new Headers();
        object.writeHttpMetadata(headers);
        headers.set("content-type", "application/zip");
        headers.set("content-disposition", `attachment; filename="Astrata-macos-${channel}.zip"`);
        return new Response(object.body, { headers });
      }
      return json({ error: "not_found" }, 404);
    }

    if (url.pathname.startsWith("/updates/")) {
      const channel = url.pathname.split("/").pop() || "stable";
      const config = channelCatalog(url).find((entry) => entry.channel === channel);
      if (!config) return json({ error: "unknown_channel", channel }, 404);
      const object = await env.RELEASE_ARTIFACTS.get(`${channel}/macos/Astrata-macos-app.zip`);
      return json({
        status: object ? "live" : "planned",
        channel,
        invite_required: config.invite_required,
        cadence: config.cadence,
        artifact_url: object ? `${url.origin}/downloads/${channel}/macos/Astrata-macos-app.zip` : null,
        message: config.message,
      });
    }

    return json({ error: "not_found" }, 404);
  },
};

function channelCatalog(url) {
  return [
    {
      channel: "edge",
      cadence: "every_build",
      invite_required: true,
      message: "Edge is for people who want every successful build and the fastest iteration cadence.",
    },
    {
      channel: "nightly",
      cadence: "nightly",
      invite_required: true,
      message: "Nightly is for testers who want the latest promoted daily build.",
    },
    {
      channel: "tester",
      cadence: "manual_promote",
      invite_required: true,
      message: "Tester is the curated prerelease lane for friendly testers before monetization.",
    },
    {
      channel: "stable",
      cadence: "manual_release",
      invite_required: false,
      message: "Stable is for public-ready releases once distribution broadens.",
    },
  ].map((entry) => ({
    ...entry,
    update_url: `${url.origin}/updates/${entry.channel}`,
  }));
}

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
    },
  });
}
