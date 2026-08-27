/* job-scout local board — vanilla JS, no framework. */

"use strict";

const STATUSES = ["new", "applied", "interview", "offer", "rejected", "filtered", "low_score", "expired"];
const EDITABLE_STATUSES = ["new", "applied", "interview", "offer", "rejected", "filtered", "low_score"];

const STATUS_COLORS = {
  new: "var(--status-new)",
  applied: "var(--status-applied)",
  interview: "var(--status-interview)",
  offer: "var(--status-offer)",
  rejected: "var(--status-rejected)",
  filtered: "var(--status-filtered)",
  low_score: "var(--status-low_score)",
  expired: "var(--status-expired)",
};

const PAGE_SIZE = 100;

const state = {
  jobs: [],
  total: 0,
  loaded: 0,
  meta: { statuses: [], sources: [], total: 0 },
  filter: "all",
  q: "",
  source: "",
  sort: "score",
};

const $ = (id) => document.getElementById(id);
const listEl = $("joblist");
const chipsEl = $("chips");
const searchEl = $("search");
const sourceEl = $("source");
const sortEl = $("sort");
const emptyEl = $("empty");
const statusLineEl = $("status-line");
const loadMoreEl = $("load-more");
const detailEl = $("detail");
const detailBodyEl = $("detail-body");
const rowTemplate = $("row-template");

/* ---------- API ---------- */

async function fetchJSON(url, options) {
  const res = await fetch(url, options);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function loadJobs(offset = 0, limit = PAGE_SIZE) {
  const params = new URLSearchParams();
  if (state.filter !== "all") params.set("status", state.filter);
  if (state.source) params.set("source", state.source);
  params.set("sort", state.sort);
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return fetchJSON("/api/jobs?" + params.toString());
}

async function patchJob(id, body) {
  return fetchJSON(`/api/jobs/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/* ---------- rendering ---------- */

function renderChips() {
  chipsEl.textContent = "";
  const all = ["all", ...STATUSES];
  for (const status of all) {
    const btn = document.createElement("button");
    btn.className = "chip" + (state.filter === status ? " is-active" : "");
    if (status !== "all") btn.style.setProperty("--chip-color", STATUS_COLORS[status]);
    btn.dataset.status = status;
    const label = document.createElement("span");
    label.textContent = status === "all" ? "All" : status;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = state.meta.statuses.find((s) => s.status === status)?.count ?? 0;
    btn.append(label, count);
    btn.addEventListener("click", () => {
      state.filter = status;
      renderChips();
      refresh();
    });
    chipsEl.appendChild(btn);
  }
}

function fmtSalary(job) {
  if (job.comp_min == null && job.comp_max == null) return "";
  const currency = job.comp_currency || "USD";
  const fmt = (v) =>
    new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(v);
  const parts = [];
  if (job.comp_min != null) parts.push(fmt(job.comp_min));
  if (job.comp_max != null) parts.push(fmt(job.comp_max));
  const interval = job.comp_interval ? ` / ${job.comp_interval}` : "";
  return parts.length === 1 ? `${parts[0]}+${interval}` : `${parts.join(" - ")}${interval}`;
}

function fmtPosted(iso) {
  if (!iso) return "unknown date";
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00" : ""));
  const days = Math.floor((Date.now() - d.getTime()) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days}d ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function jobMatchesQuery(job) {
  if (!state.q) return true;
  const q = state.q.toLowerCase();
  return job.title.toLowerCase().includes(q) || job.company.toLowerCase().includes(q);
}

function sortJobs(jobs) {
  const sorted = [...jobs];
  if (state.sort === "date") {
    sorted.sort((a, b) => (b.date_posted || "").localeCompare(a.date_posted || ""));
  } else if (state.sort === "salary") {
    sorted.sort((a, b) => (b.comp_max || 0) - (a.comp_max || 0));
  } else {
    sorted.sort((a, b) => b.score - a.score);
  }
  return sorted;
}

function buildRow(job) {
  const li = rowTemplate.content.cloneNode(true);
  const root = li.querySelector(".job");

  const score = root.querySelector(".job-score");
  score.textContent = job.score;
  score.classList.toggle("high", job.score >= 55);
  score.classList.toggle("mid", job.score >= 30 && job.score < 55);

  root.style.setProperty("--status", STATUS_COLORS[job.status] || "var(--text-faint)");

  root.querySelector(".job-title").textContent = job.title;

  const statusPill = root.querySelector(".job-status");
  statusPill.textContent = job.status;
  statusPill.style.setProperty("--status", STATUS_COLORS[job.status] || "var(--text-faint)");

  const metaParts = [];
  if (job.company) metaParts.push(job.company);
  if (job.location && job.location !== "Unknown") metaParts.push(job.location);
  const salary = fmtSalary(job);
  if (salary) metaParts.push(salary);
  if (job.date_posted) metaParts.push(`posted ${fmtPosted(job.date_posted)}`);
  metaParts.push(job.source);
  root.querySelector(".job-meta").textContent = metaParts.join("  ·  ");

  if (job.notes) {
    const notes = root.querySelector(".job-notes");
    notes.textContent = `notes: ${job.notes}`;
    notes.hidden = false;
  }

  const select = root.querySelector(".status-select");
  const selectableStatuses = job.status === "expired" ? ["expired"] : EDITABLE_STATUSES;
  for (const status of selectableStatuses) {
    const opt = document.createElement("option");
    opt.value = status;
    opt.textContent = status;
    opt.selected = status === job.status;
    select.appendChild(opt);
  }
  select.disabled = job.status === "expired";
  select.addEventListener("change", async () => {
    select.disabled = true;
    try {
      await patchJob(job.id, { status: select.value });
      job.status = select.value;
      await refresh();
    } catch (err) {
      statusLineEl.textContent = `update failed: ${err.message}`;
      select.disabled = false;
    }
  });

  root.addEventListener("click", (e) => {
    if (e.target.closest(".status-select")) return;
    openDetail(job);
  });

  return li;
}

function render(jobs) {
  listEl.textContent = "";
  const visible = sortJobs(jobs.filter(jobMatchesQuery));
  const fragments = visible.map(buildRow);
  for (const f of fragments) listEl.appendChild(f);

  emptyEl.hidden = visible.length > 0;
  const label = state.filter === "all" ? "all jobs" : `status "${state.filter}"`;
  statusLineEl.textContent = `${visible.length} of ${state.total} shown (${label})`;
  loadMoreEl.hidden = state.loaded >= state.total;
}

async function refresh() {
  try {
    await loadPage(0);
    const meta = await fetchJSON("/api/meta");
    state.meta = meta;
    renderChips();
    populateSources(meta.sources);
  } catch (err) {
    statusLineEl.textContent = `load failed: ${err.message}`;
  }
}

async function loadPage(offset) {
  const data = await loadJobs(offset, PAGE_SIZE);
  state.jobs = offset === 0 ? data.jobs : state.jobs.concat(data.jobs);
  state.loaded = state.jobs.length;
  state.total = data.total;
  render(state.jobs);
}

async function loadMore() {
  try {
    await loadPage(state.loaded);
  } catch (err) {
    statusLineEl.textContent = `load failed: ${err.message}`;
  }
}

/* ---------- source select ---------- */

function populateSources(sources) {
  const current = sourceEl.value;
  sourceEl.textContent = "";
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "All sources";
  sourceEl.appendChild(all);
  for (const s of sources) {
    const opt = document.createElement("option");
    opt.value = s;
    opt.textContent = s;
    sourceEl.appendChild(opt);
  }
  sourceEl.value = current;
}

/* ---------- detail dialog ---------- */

function openDetail(job) {
  const parts = [];
  const meta = [];
  if (job.company) meta.push(job.company);
  if (job.location && job.location !== "Unknown") meta.push(job.location);
  if (job.search_term) meta.push(`search: ${job.search_term}`);
  const salary = fmtSalary(job);
  if (salary) meta.push(salary);
  if (job.date_posted) meta.push(`posted ${fmtPosted(job.date_posted)}`);
  meta.push(`${job.source} · score ${job.score}/100`);

  parts.push(`<h2 class="detail-title">${escapeHtml(job.title)}</h2>`);
  parts.push(`<p class="detail-sub">${escapeHtml(meta.join(" · "))}</p>`);
  if (job.description) {
    parts.push(`<p class="detail-desc">${escapeHtml(job.description)}</p>`);
  }
  if (job.notes) {
    parts.push(`<p class="detail-desc" style="color:var(--text-dim)">notes: ${escapeHtml(job.notes)}</p>`);
  }
  if (job.url) {
    parts.push(`<a class="detail-link" href="${escapeAttr(job.url)}" target="_blank" rel="noopener">Open original listing →</a>`);
  }
  detailBodyEl.innerHTML = parts.join("");
  detailEl.showModal();
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeAttr(s) {
  return escapeHtml(s).replace(/'/g, "&#39;");
}

/* ---------- events ---------- */

searchEl.addEventListener("input", () => {
  state.q = searchEl.value.trim();
  render(state.jobs);
});

sourceEl.addEventListener("change", () => {
  state.source = sourceEl.value;
  refresh();
});

sortEl.addEventListener("change", () => {
  state.sort = sortEl.value;
  refresh();
});

loadMoreEl.addEventListener("click", loadMore);

$("detail-close").addEventListener("click", () => detailEl.close());
detailEl.addEventListener("click", (e) => {
  if (e.target === detailEl) detailEl.close();
});

/* ---------- boot ---------- */

(async function init() {
  try {
    const meta = await fetchJSON("/api/meta");
    state.meta = meta;
    renderChips();
    populateSources(meta.sources);
    await loadPage(0);
  } catch (err) {
    statusLineEl.textContent = `failed to load: ${err.message}`;
  }
})();
