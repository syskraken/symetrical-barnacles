// Pull the latest release info from GitHub so the version, download link,
// and file size update on their own whenever a new release is published.
// The static href in the HTML stays as a fallback if the API is unreachable.

const REPO = "syskraken/symetrical-barnacles";

async function loadLatestRelease() {
  try {
    const res = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, {
      headers: { Accept: "application/vnd.github+json" }
    });
    if (!res.ok) return; // rate-limited / offline → keep the static fallback

    const data = await res.json();
    const assets = data.assets || [];
    const asset = assets.find(a => a.name.toLowerCase().endsWith(".exe")) || assets[0];

    // Version label
    const verEl = document.getElementById("dlVersion");
    if (verEl && data.tag_name) verEl.textContent = `Latest release · ${data.tag_name}`;

    if (asset) {
      // Real download link for the current release asset
      const btn = document.getElementById("dlBtn");
      if (btn) {
        btn.href = asset.browser_download_url;
        btn.setAttribute("download", asset.name);
      }
      // File size
      const sizeEl = document.getElementById("dlSize");
      if (sizeEl && asset.size) sizeEl.textContent = (asset.size / 1048576).toFixed(1) + " MB";
    }
  } catch (e) {
    // Network error — the page already has a working static download link.
  }
}

loadLatestRelease();
