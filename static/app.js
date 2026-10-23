"use strict";

const $ = (id) => document.getElementById(id);
const screens = ["join", "lobby", "loading", "round", "reveal", "final"];

let config = { mode: "photos", apiKey: "", baseUrls: [] };
// Remember the seat in localStorage too, so closing the home-screen app and reopening it rejoins.
let seat = readSeat();
let pid = seat.pid, code = seat.code;
let state = null;
let clockOffset = 0;      // serverTime - local time, in seconds
let renderedKey = "";     // which round/phase the heavy UI was last built for
let polling = false;

// ------------------------------------------------------------ utilities

function readSeat() {
  for (const store of ["sessionStorage", "localStorage"]) {
    try {
      const s = JSON.parse(window[store].getItem("pinpoint-seat") || "null");
      if (s && s.pid && s.code) return s;
    } catch {}
  }
  return {};
}
function saveSeat(newPid, newCode) {
  pid = newPid; code = newCode;
  for (const store of ["sessionStorage", "localStorage"]) {
    try {
      if (pid) window[store].setItem("pinpoint-seat", JSON.stringify({ pid, code }));
      else window[store].removeItem("pinpoint-seat");
    } catch {}
  }
}
function localGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem(k, v); } catch {} }

function show(name) {
  for (const s of screens) $("screen-" + s).hidden = s !== name;
}

function toast(msg, ms = 2600) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), ms);
}

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "style") e.style.cssText = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) e.append(kid);
  return e;
}

function avatar(p) {
  return el("span", { class: "avatar", style: `background:${p.color}` }, (p.name[0] || "?").toUpperCase());
}

function fmtDistance(km) {
  if (km == null) return "No guess";
  if (km < 1) return `${Math.round(km * 1000)} m`;
  if (km < 10) return `${km.toFixed(1)} km`;
  return `${Math.round(km).toLocaleString("en-US")} km`;
}

const fmtPts = (n) => Math.round(n).toLocaleString("en-US");
const now = () => Date.now() / 1000 + clockOffset;
const isHost = () => state && state.host === pid;
const player = (id) => state.players.find((p) => p.id === id);
const currentRound = () => state.rounds[state.roundIndex];

async function api(action, body = {}) {
  const res = await fetch("/api/" + action, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pid, code, ...body }),
  });
  const data = await res.json();
  if (data.gone) return leaveToJoin("That game has ended. Start a new one or join another code.");
  if (!res.ok) throw new Error(data.error || "Something went wrong.");
  if (data.phase) apply(data);
  return data;
}

// ------------------------------------------------------------ polling

async function poll() {
  if (!pid || !code) return renderJoin();
  if (polling) return;
  polling = true;
  try {
    // Give up on a stuck request after 10 s so the next poll can try again.
    const res = await fetch(`/api/state?code=${encodeURIComponent(code)}&pid=${encodeURIComponent(pid)}`,
      { cache: "no-store", signal: AbortSignal.timeout(10000) });
    const data = await res.json();
    if (data.gone) return leaveToJoin("That game has ended. Start a new one or join another code.");
    apply(data);
  } catch {
    toast("Lost connection to the game. Retrying…", 1500);
  } finally {
    polling = false;
  }
}

function apply(s) {
  state = s;
  clockOffset = s.serverTime - Date.now() / 1000;
  if (pid && !s.you) return leaveToJoin("You were away too long and left the game. Join again with the code.", s.code);
  render();
}

function leaveToJoin(message, keepCode) {
  saveSeat(null, null);
  state = null;
  renderedKey = "";
  if (keepCode) $("code-input").value = keepCode;
  renderJoin();
  if (message) toast(message, 4000);
}

// ------------------------------------------------------------ rendering

function render() {
  if (!state.you) return renderJoin();
  const key = `${state.matchId}:${state.phase}:${state.roundIndex}`;
  const fresh = key !== renderedKey;
  renderedKey = key;
  switch (state.phase) {
    case "lobby": return renderLobby();
    case "loading": return renderLoading();
    case "round": return renderRound(fresh);
    case "reveal": return renderReveal(fresh);
    case "final": return renderFinal(fresh);
  }
}

function renderJoin() {
  show("join");
  renderedKey = "";
  if (!$("name-input").value) $("name-input").value = localGet("pinpoint-name") || "";
}

function inviteUrl() {
  const base = config.baseUrls[0] || location.origin;
  return `${base}/?code=${state.code}`;
}

function renderLobby() {
  show("lobby");
  const online = state.players.filter((p) => p.online);
  $("lobby-mode").textContent = config.mode === "streetview" ? "Google Street View" : "Wikimedia photo mode";
  $("player-count").textContent = `${online.length} / 10`;

  const list = $("lobby-players");
  list.replaceChildren(...online.map((p) =>
    el("li", {}, avatar(p), el("span", { class: "name" }, p.name),
      el("span", { class: "tag" }, [p.id === state.host ? "Host" : "", p.id === pid ? " · You" : ""].join("").replace(/^ · /, "")))));
  if (online.length < 2) list.append(el("li", { class: "empty" }, "Waiting for at least one more player…"));

  $("game-code").textContent = state.code;
  $("invite-url").textContent = inviteUrl();

  const host = isHost();
  const s = state.settings;
  setSeg("set-rounds", String(s.rounds), host);
  setSeg("set-time", String(s.timeLimit), host);
  setSeg("set-move", String(s.noMove), host);
  $("move-note").textContent = config.mode === "streetview"
    ? (s.noMove ? "Players can look around and zoom but not walk." : "Players can walk along the roads.")
    : "Photo mode shows 3–5 photos taken near the spot. Movement doesn't apply.";
  $("host-tag").textContent = host ? "You're the host" : `${player(state.host)?.name || "The host"} picks the settings`;
  $("start-btn").hidden = !host;
  $("start-btn").disabled = online.length < 2;
  $("lobby-hint").textContent = host
    ? (online.length < 2 ? "Invite at least one friend to start." : "")
    : `Waiting for ${player(state.host)?.name || "the host"} to start the match.`;
}

function setSeg(id, value, enabled) {
  for (const b of $(id).querySelectorAll("button")) {
    b.setAttribute("aria-pressed", String(b.dataset.v === value));
    b.disabled = !enabled;
  }
}

function renderLoading() {
  show("loading");
  const n = state.roundIndex + 1;
  $("loading-text").textContent = state.roundIndex === 0 ? "Finding places on Earth…" : `Loading round ${n}…`;
  $("loading-error").hidden = !state.error;
  $("loading-error").textContent = state.error;
  $("loading-lobby-btn").hidden = !(state.error && isHost());
}

// ---- round

let guessMap, guessMarker, guessLatLng = null;
let panorama = null, photoIndex = 0;

function renderRound(fresh) {
  show("round");
  const rnd = currentRound();
  if (fresh) setupRound(rnd);

  $("hud-round").textContent = `${state.roundIndex + 1} / ${state.settings.rounds}`;
  $("hud-timer-box").hidden = !rnd.endsAt;

  const playing = new Set(rnd.playing);
  $("hud-players").replaceChildren(...state.players.filter((p) => playing.has(p.id)).map((p) => {
    const done = rnd.guessed.includes(p.id);
    return el("span", { class: "hud-player" + (done ? " done" : ""), title: done ? `${p.name} has guessed` : `${p.name} is still looking` },
      avatar(p), p.id === pid ? "You" : p.name, done ? el("span", { class: "check" }, "✓") : null);
  }));

  const mine = rnd.myGuess;
  const spectating = !playing.has(pid);
  $("guess-panel").hidden = !!mine || spectating;
  $("open-map-btn").hidden = !!mine || spectating;
  const banner = $("guessed-banner");
  if (spectating) {
    banner.hidden = false;
    banner.textContent = "You joined mid-round. You'll play from the next round.";
  } else if (mine) {
    const left = rnd.playing.filter((id) => !rnd.guessed.includes(id)).length;
    banner.hidden = false;
    banner.textContent = left ? `Guess locked in. Waiting for ${left} more player${left === 1 ? "" : "s"}…` : "Everyone has guessed!";
  } else {
    banner.hidden = true;
  }
}

function setupRound(rnd) {
  guessLatLng = null;
  $("guess-btn").disabled = true;
  $("guess-btn").textContent = "Place a pin on the map";
  $("guess-panel").classList.remove("big", "pinned");

  if (!guessMap) {
    guessMap = L.map("guess-map", { worldCopyJump: true, zoomControl: true, attributionControl: true }).setView([20, 0], 1);
    addTiles(guessMap);
    guessMap.on("click", (e) => {
      guessLatLng = e.latlng.wrap();
      if (!guessMarker) guessMarker = L.marker(e.latlng, { icon: pinIcon(player(pid)) }).addTo(guessMap);
      guessMarker.setLatLng(e.latlng);
      $("guess-btn").disabled = false;
      $("guess-btn").textContent = "Guess";
      $("guess-panel").classList.add("pinned");
    });
  }
  if (guessMarker) { guessMarker.remove(); guessMarker = null; }
  guessMap.setView([20, 0], 1);
  setTimeout(() => guessMap.invalidateSize(), 50);

  if (config.mode === "streetview" && rnd.panoId) setupPano(rnd.panoId);
  else setupPhotos(rnd.photos || []);
}

function addTiles(map) {
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
}

function pinIcon(p) {
  const color = p ? p.color : "#f2a93b";
  const letter = p ? (p.name[0] || "?").toUpperCase() : "";
  return L.divIcon({ className: "", iconSize: [30, 30], iconAnchor: [15, 34],
    html: `<div class="pin" style="background:${color}"><b>${letter.replace(/[<>&]/g, "")}</b></div>` });
}

const answerIcon = () => L.divIcon({ className: "", iconSize: [34, 34], iconAnchor: [17, 17], html: '<div class="answer-pin"></div>' });

let mapsReady = null;
function loadGoogleMaps() {
  if (!mapsReady) {
    mapsReady = new Promise((resolve, reject) => {
      window.__pinpointMaps = resolve;
      const s = document.createElement("script");
      s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(config.apiKey)}&v=weekly&loading=async&callback=__pinpointMaps`;
      s.onerror = reject;
      document.head.append(s);
    });
  }
  return mapsReady;
}

async function setupPano(panoId) {
  $("photos").hidden = true;
  $("pano").hidden = false;
  $("return-btn").hidden = false;
  $("return-btn").onclick = () => panorama && panorama.setPano(panoId);
  try {
    await loadGoogleMaps();
  } catch {
    toast("Couldn't load Google Street View. Check the API key in config.json.");
    return;
  }
  const noMove = state.settings.noMove;
  const opts = {
    pano: panoId, pov: { heading: Math.random() * 360, pitch: 0 }, zoom: 0,
    addressControl: false, showRoadLabels: false, fullscreenControl: false, motionTracking: false,
    motionTrackingControl: false, enableCloseButton: false, linksControl: !noMove, clickToGo: !noMove,
    panControl: true, zoomControl: true,
  };
  if (!panorama) panorama = new google.maps.StreetViewPanorama($("pano"), opts);
  else panorama.setOptions(opts);
}

function setupPhotos(photos) {
  $("pano").hidden = true;
  $("photos").hidden = false;
  $("return-btn").hidden = true;
  photoIndex = 0;
  const thumbs = $("photo-thumbs");
  thumbs.replaceChildren(...photos.map((p, i) =>
    el("button", { type: "button", "aria-label": `Photo ${i + 1}`, onclick: () => showPhoto(i) },
      el("img", { src: p.url, alt: "" }))));
  showPhoto(0);
}

function showPhoto(i) {
  const photos = currentRound().photos || [];
  if (!photos.length) return;
  photoIndex = (i + photos.length) % photos.length;
  $("photo-main").src = photos[photoIndex].url;
  [...$("photo-thumbs").children].forEach((b, j) => b.setAttribute("aria-current", String(j === photoIndex)));
}

function tickTimer() {
  if (!state || state.phase !== "round") return;
  const rnd = currentRound();
  if (!rnd || !rnd.endsAt) return;
  const left = Math.max(0, Math.ceil(rnd.endsAt - now()));
  $("hud-timer").textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  $("hud-timer").classList.toggle("urgent", left <= 10);
}

// ---- reveal

let revealMap, revealLayer;

function renderReveal(fresh) {
  show("reveal");
  const rnd = currentRound();
  const last = state.roundIndex + 1 >= state.settings.rounds;

  if (fresh) {
    $("reveal-round").textContent = `Round ${state.roundIndex + 1} of ${state.settings.rounds}`;
    $("reveal-place").textContent = `Near ${rnd.answer.place}`;

    if (!revealMap) {
      revealMap = L.map("reveal-map", { worldCopyJump: true });
      addTiles(revealMap);
    }
    if (revealLayer) revealLayer.remove();
    revealLayer = L.layerGroup().addTo(revealMap);
    const ans = [rnd.answer.lat, rnd.answer.lng];
    const pts = [ans];
    for (const [id, g] of Object.entries(rnd.guesses)) {
      const p = player(id);
      // Draw on whichever world copy is closest to the answer so lines don't wrap the globe.
      let lng = g.lng;
      while (lng - ans[1] > 180) lng -= 360;
      while (ans[1] - lng > 180) lng += 360;
      const ll = [g.lat, lng];
      pts.push(ll);
      L.polyline([ll, ans], { color: p ? p.color : "#fff", weight: 3, dashArray: "6 8", opacity: .9 }).addTo(revealLayer);
      L.marker(ll, { icon: pinIcon(p) }).bindTooltip(`${p ? p.name : "Player"} · ${fmtDistance(g.km)}`).addTo(revealLayer);
    }
    L.marker(ans, { icon: answerIcon(), zIndexOffset: 1000 }).bindTooltip(rnd.answer.place).addTo(revealLayer);
    setTimeout(() => {
      revealMap.invalidateSize();
      if (pts.length > 1) revealMap.fitBounds(pts, { padding: [60, 60], maxZoom: 12 });
      else revealMap.setView(ans, 5);
    }, 50);

    const credits = rnd.credits || [];
    $("credits").hidden = !credits.length;
    $("credit-list").replaceChildren(...credits.map((c) => el("li", {},
      el("a", { href: c.page, target: "_blank", rel: "noopener" }, c.title), ` by ${c.author}${c.license ? ", " + c.license : ""}`)));

    const results = rnd.playing.map((id) => ({ id, p: player(id), g: rnd.guesses[id] }))
      .filter((r) => r.p)
      .sort((a, b) => (b.g ? b.g.points : -1) - (a.g ? a.g.points : -1));
    $("reveal-list").replaceChildren(...results.map(({ p, g }) => el("li", {},
      avatar(p),
      el("span", { class: "name" }, p.id === pid ? `${p.name} (you)` : p.name),
      el("span", { class: "pts mono" }, g ? `+${fmtPts(g.points)}` : "0", el("small", {}, `${fmtPts(p.score)} total`)),
      el("span", { class: "detail" }, g ? `${fmtDistance(g.km)} away · ${Math.round(g.seconds)}s` : "Didn't guess in time"),
      el("span", { class: "bar" }, el("i", { style: `width:${g ? (g.points / 50).toFixed(1) : 0}%` })))));
  }

  const host = isHost();
  $("next-btn").hidden = !host;
  $("next-btn").textContent = last ? "See final results" : "Next round";
  $("next-wait").textContent = host ? "" : `Waiting for ${player(state.host)?.name || "the host"} to continue…`;
}

// ---- final

function renderFinal(fresh) {
  show("final");
  if (fresh) {
    const ranked = state.players.filter((p) => state.rounds.some((r) => r.playing.includes(p.id)))
      .sort((a, b) => b.score - a.score);
    const order = [1, 0, 2]; // silver, gold, bronze left to right
    $("podium").replaceChildren(...ranked.slice(0, 3).map((p, i) => el("div", {
      class: "step" + (i === 0 ? " first" : ""), style: `order:${order[i]}`,
    }, el("span", { class: "place" }, String(i + 1)), avatar(p), el("span", { class: "who" }, p.name),
      el("span", { class: "total" }, fmtPts(p.score)))));

    const head = el("tr", {}, el("th", {}, "Player"),
      state.rounds.map((r, i) => el("th", { title: r.answer?.place || "" }, `R${i + 1}`)), el("th", {}, "Total"));
    const rows = ranked.map((p) => el("tr", {}, el("td", {}, p.name),
      state.rounds.map((r) => el("td", { title: r.guesses?.[p.id] ? fmtDistance(r.guesses[p.id].km) : "No guess" },
        r.guesses?.[p.id] ? fmtPts(r.guesses[p.id].points) : "–")),
      el("td", { class: "total" }, fmtPts(p.score))));
    const places = el("tr", {}, el("td", { class: "muted" }, "Location"),
      state.rounds.map((r) => el("td", { class: "muted small" }, (r.answer?.place || "").split(",")[0])), el("td"));
    $("final-table").replaceChildren(el("thead", {}, head), el("tbody", {}, rows, places));
  }
  $("again-btn").hidden = !isHost();
  $("again-wait").textContent = isHost() ? "Everyone goes back to the lobby with scores reset." : `Waiting for ${player(state.host)?.name || "the host"} to start another match…`;
}

// ------------------------------------------------------------ events

async function enter(action) {
  const name = $("name-input").value.trim();
  const wanted = $("code-input").value.trim().toUpperCase();
  $("join-error").hidden = true;
  const fail = (msg) => { $("join-error").textContent = msg; $("join-error").hidden = false; };
  if (!name) return fail("Type your name first.");
  if (action === "join" && !/^[A-Z]{4}$/.test(wanted)) return fail("Game codes are 4 letters, like KBRT.");
  localSet("pinpoint-name", name);
  try {
    const res = await fetch("/api/" + action, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, code: wanted }) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);
    saveSeat(data.pid, data.code);
    history.replaceState(null, "", "/");
    await poll();
  } catch (err) {
    fail(err.message || "Couldn't reach the game server. Check your connection.");
  }
}
$("join-form").addEventListener("submit", (e) => { e.preventDefault(); enter("join"); });
$("create-btn").addEventListener("click", () => enter("create"));
$("code-input").addEventListener("input", (e) => { e.target.value = e.target.value.toUpperCase().replace(/[^A-Z]/g, "").slice(0, 4); });

$("share-btn").addEventListener("click", async () => {
  const url = inviteUrl();
  const text = `Join my Pinpoint game! Code ${state.code}`;
  if (navigator.share) {
    try { await navigator.share({ title: "Pinpoint", text, url }); return; } catch (err) { if (err.name === "AbortError") return; }
  }
  try { await navigator.clipboard.writeText(url); toast("Invite link copied"); } catch { toast(url, 6000); }
});

function bindSeg(id, key, parse) {
  $(id).addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || !isHost()) return;
    api("settings", { settings: { [key]: parse(b.dataset.v) } }).catch((err) => toast(err.message));
  });
}
bindSeg("set-rounds", "rounds", Number);
bindSeg("set-time", "timeLimit", Number);
bindSeg("set-move", "noMove", (v) => v === "true");

$("start-btn").addEventListener("click", () => api("start").catch((err) => {
  $("lobby-error").textContent = err.message;
  $("lobby-error").hidden = false;
}));
$("leave-btn").addEventListener("click", async () => {
  await api("leave").catch(() => {});
  leaveToJoin();
});
$("loading-lobby-btn").addEventListener("click", () => api("lobby").catch((err) => toast(err.message)));

$("guess-btn").addEventListener("click", async () => {
  if (!guessLatLng) return;
  $("guess-btn").disabled = true;
  try {
    await api("guess", { lat: guessLatLng.lat, lng: guessLatLng.lng });
  } catch (err) {
    toast(err.message);
    $("guess-btn").disabled = false;
  }
});
$("map-size-btn").addEventListener("click", () => {
  $("guess-panel").classList.toggle("big");
  setTimeout(() => guessMap && guessMap.invalidateSize(), 220);
});
$("open-map-btn").addEventListener("click", () => {
  $("guess-panel").classList.add("big");
  setTimeout(() => guessMap && guessMap.invalidateSize(), 220);
});
$("guess-panel").addEventListener("transitionend", () => guessMap && guessMap.invalidateSize());
$("photo-prev").addEventListener("click", () => showPhoto(photoIndex - 1));
$("photo-next").addEventListener("click", () => showPhoto(photoIndex + 1));

document.addEventListener("keydown", (e) => {
  if (state?.phase !== "round" || e.target.tagName === "INPUT") return;
  if (e.key === "ArrowLeft" && config.mode === "photos") showPhoto(photoIndex - 1);
  if (e.key === "ArrowRight" && config.mode === "photos") showPhoto(photoIndex + 1);
  if ((e.key === " " || e.key === "Enter") && guessLatLng && !$("guess-btn").disabled) {
    e.preventDefault();
    $("guess-btn").click();
  }
});

$("next-btn").addEventListener("click", () => api("next").catch((err) => toast(err.message)));
$("again-btn").addEventListener("click", () => api("lobby").catch((err) => toast(err.message)));

// ------------------------------------------------------------ boot

(async function boot() {
  try {
    config = await (await fetch("/api/config")).json();
  } catch {}
  const linkCode = new URLSearchParams(location.search).get("code");
  if (linkCode) {
    $("code-input").value = linkCode.toUpperCase().slice(0, 4);
    if (code && code !== $("code-input").value) saveSeat(null, null); // invited to a different game
  }
  await poll();
  setInterval(poll, 1000);
  setInterval(tickTimer, 250);
})();
