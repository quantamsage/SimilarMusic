// Recommendation forms: sends them to the JSON API, shows progress while
// the songs are found and analysed, then opens the results page. Without
// JavaScript the same forms post to /recommend and work the same way.

const API = "/api/recommendations";
/** Give up waiting after this long (the server's AI timeout is shorter). */
const TIMEOUT_MS = 5 * 60 * 1000;
const SLOW_AFTER_S = 70;

/** What the user can do about each error code. */
export const ERROR_HINTS = {
  invalid_url: "Copy the song's share link from Spotify or YouTube and paste it here.",
  unsupported_platform: "Spotify track links and YouTube video links are supported.",
  song_not_found: "Check that the link points to a single song that is still available.",
  provider_not_configured: "The server is missing API credentials for this music service. See the README.",
  provider_unavailable: "A music service didn't respond as expected. Try again in a moment.",
  rate_limited: "Too many requests were made to a music service. Wait a minute and try again.",
  no_recommendations: "This song doesn't have enough listening data yet. Try a more widely known song.",
  ai_not_configured: "The server has no AI API key configured. See the README.",
  ai_failed: "The AI analysis didn't complete. Try again in a moment.",
  network: "Check that the server is running and your connection works, then try again.",
  timeout: "The server took too long. Try again; results for this song may already be ready.",
};

/** Checks a song link before sending it (the server checks it again). */
export function validateLink(value) {
  const text = value.trim();
  if (!text) return { code: "invalid_url", message: "Paste a link to a song first." };
  // Browsers differ in what they accept as a URL (Chromium allows spaces in
  // host names), so check the basics explicitly.
  if (/\s/.test(text)) return { code: "invalid_url", message: "That doesn't look like a link." };
  let url;
  try {
    url = new URL(/^[a-z][a-z0-9+.-]*:/i.test(text) ? text : `https://${text}`);
  } catch {
    return { code: "invalid_url", message: "That doesn't look like a link." };
  }
  if (!/^https?:$/.test(url.protocol)) return { code: "invalid_url", message: "Song links start with https://." };
  if (!url.hostname.includes(".")) return { code: "invalid_url", message: "That doesn't look like a link." };
  return null;
}

/** Turns an API error response into {code, message}. */
export async function readError(response) {
  try {
    const body = await response.json();
    if (body && typeof body.message === "string") return { code: body.error || "error", message: body.message };
    if (body && Array.isArray(body.detail)) return { code: "invalid_url", message: "Paste a valid song link." };
  } catch {
    // fall through
  }
  return { code: "error", message: `Something went wrong (HTTP ${response.status}). Please try again.` };
}

// ----- Page wiring (skipped when imported by tests) -----

const $ = (id) => document.getElementById(id);
let active = null;

function showError(form, error) {
  const box = $("form-error");
  if (!box) return;
  box.querySelector("[data-error-message]").textContent = error.message;
  const hint = box.querySelector("[data-error-hint]");
  hint.textContent = ERROR_HINTS[error.code] || "";
  hint.hidden = !hint.textContent;
  box.hidden = false;
  const input = form.querySelector('input[name="url"]');
  if (input && ["invalid_url", "unsupported_platform", "song_not_found"].includes(error.code)) {
    input.setAttribute("aria-invalid", "true");
    input.focus();
  } else {
    box.scrollIntoView({ block: "nearest" });
  }
}

function clearError(form) {
  const box = $("form-error");
  if (box) box.hidden = true;
  form.querySelector('input[name="url"]')?.removeAttribute("aria-invalid");
}

function showLoading() {
  const overlay = $("loading");
  const steps = [...overlay.querySelectorAll("[data-after]")];
  const started = Date.now();
  const tick = () => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    $("loading-elapsed").textContent = String(seconds);
    let current = 0;
    steps.forEach((step, i) => {
      if (seconds >= Number(step.dataset.after)) current = i;
    });
    steps.forEach((step, i) => {
      step.classList.toggle("is-done", i < current);
      step.classList.toggle("is-current", i === current);
      if (i === current) step.setAttribute("aria-current", "step");
      else step.removeAttribute("aria-current");
    });
    $("loading-slow").hidden = seconds < SLOW_AFTER_S;
  };
  tick();
  const timer = setInterval(tick, 500);
  overlay.hidden = false;
  document.body.classList.add("is-loading");
  $("loading-cancel").focus();
  return () => {
    clearInterval(timer);
    overlay.hidden = true;
    document.body.classList.remove("is-loading");
  };
}

async function submit(form) {
  if (active) return;
  clearError(form);
  const data = new FormData(form);
  const songId = data.get("song_id");
  const url = (data.get("url") || "").trim();
  if (!songId) {
    const problem = validateLink(url);
    if (problem) {
      showError(form, problem);
      return;
    }
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort("timeout"), TIMEOUT_MS);
  const hide = showLoading();
  active = controller;
  try {
    const response = await fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(songId ? { song_id: Number(songId) } : { url }),
      signal: controller.signal,
    });
    if (!response.ok) {
      hide();
      showError(form, await readError(response));
      return;
    }
    const result = await response.json();
    window.location.assign(`/results/${encodeURIComponent(result.request_id)}`);
  } catch (err) {
    hide();
    if (controller.signal.aborted && controller.signal.reason === "cancel") return;
    const code = controller.signal.aborted ? "timeout" : "network";
    showError(form, { code, message: code === "timeout" ? "That took too long." : "Couldn't reach the server." });
  } finally {
    clearTimeout(timeout);
    active = null;
  }
}

/** Replaces artwork that fails to load (e.g. an expired image link) with the letter placeholder. */
function useArtworkPlaceholder(img) {
  const placeholder = document.createElement("div");
  placeholder.className = img.className.replace("artwork-", "artwork-placeholder artwork-");
  placeholder.setAttribute("aria-hidden", "true");
  placeholder.textContent = (img.alt.replace(/^Artwork for /, "")[0] || "♪").toUpperCase();
  img.replaceWith(placeholder);
}

function init() {
  document.addEventListener(
    "error",
    (event) => {
      if (event.target instanceof HTMLImageElement && event.target.classList.contains("artwork")) useArtworkPlaceholder(event.target);
    },
    true,
  );
  for (const img of document.querySelectorAll("img.artwork")) {
    if (img.complete && img.naturalWidth === 0) useArtworkPlaceholder(img);
  }
  for (const form of document.querySelectorAll("form[data-recommend]")) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submit(form);
    });
  }
  $("loading-cancel")?.addEventListener("click", () => active?.abort("cancel"));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && active) active.abort("cancel");
  });
  // Coming back with the browser's Back button must not show a stale overlay.
  window.addEventListener("pageshow", () => {
    const overlay = $("loading");
    if (overlay) overlay.hidden = true;
    document.body.classList.remove("is-loading");
  });
}

if (typeof document !== "undefined") init();
