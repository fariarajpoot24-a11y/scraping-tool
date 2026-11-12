const form = document.querySelector("#scan-form");
const urlInput = document.querySelector("#url");
const pagesSelect = document.querySelector("#max-pages");
const scanBtn = document.querySelector("#scan-btn");
const bar = document.querySelector("#bar");
const statusEl = document.querySelector("#status");
const results = document.querySelector("#results");
const output = document.querySelector("#output");

let lastResult = null;

/* Build elements with textContent only, so scraped text can never run as HTML. */
function h(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  kids.flat().filter((k) => k !== null && k !== undefined && k !== false).forEach((k) => node.append(k));
  return node;
}

function safeHref(url) {
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : "#";
  } catch {
    return "#";
  }
}

function link(text, href) {
  return h("a", { href: safeHref(href), target: "_blank", rel: "noopener noreferrer" }, text);
}

function block(title, hint, ...content) {
  return h("section", { class: "block" },
    h("div", {}, h("h2", {}, title), h("p", { class: "hint" }, hint)),
    h("div", {}, ...content));
}

function empty(text) {
  return h("p", { class: "empty" }, text);
}

/* ---------- sections ---------- */

function renderOverview(data) {
  const p = data.purpose;
  return block("Purpose", "What the site says it is for.",
    h("p", { class: "site-name" }, data.site.name),
    data.site.tagline ? h("p", { class: "tagline" }, data.site.tagline) : null,
    p.summary
      ? h("p", { class: "purpose" }, p.summary)
      : empty("No description found on this site."),
    p.summary ? h("p", { class: "source" }, `Source: ${p.source}`) : null,
    p.about && p.about !== p.summary ? h("p", {}, p.about) : null,
    h("p", { class: "source" }, "Address: ", link(data.url, data.url)));
}

function renderPages(data) {
  const categories = ["all", "about", "services", "contact", "other"];
  const list = h("ul", { class: "pages" });
  const filters = h("div", { class: "filters" });

  function draw(category) {
    list.replaceChildren();
    const shown = data.pages.filter((p) => category === "all" || p.category === category);
    if (!shown.length) list.append(h("li", {}, empty("No pages in this group.")));
    shown.forEach((p) => list.append(h("li", {},
      h("span", { class: "label" }, link(p.label, p.url)),
      h("span", { class: `tag ${p.category}` }, p.category),
      h("span", { class: "url" }, p.url))));
    filters.querySelectorAll("button").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.cat === category)));
  }

  categories.forEach((cat) => {
    const count = cat === "all" ? data.pages.length : data.pages.filter((p) => p.category === cat).length;
    const btn = h("button", { type: "button", "data-cat": cat, "aria-pressed": "false" },
      `${cat[0].toUpperCase() + cat.slice(1)} (${count})`);
    btn.addEventListener("click", () => draw(cat));
    filters.append(btn);
  });
  draw("all");

  return block("Pages", `${data.pages.length} internal pages found.`,
    data.pages.length ? [filters, list] : empty("No internal links were found on this site."));
}

function renderServices(data) {
  return block("Services", "Offerings found in headings, lists and menus.",
    data.services.length
      ? h("ul", { class: "chips" }, data.services.map((s) => h("li", {}, s)))
      : empty("No services found. Some sites load their content with JavaScript, which this tool cannot read."));
}

function renderContact(data) {
  const c = data.contact;
  const rows = [];
  const add = (label, items) => {
    if (!items.length) return;
    rows.push(h("dt", {}, label));
    rows.push(h("dd", {}, ...items.flatMap((item, i) => (i ? [h("br"), item] : [item]))));
  };
  add("Email", c.emails.map((e) => h("a", { href: `mailto:${encodeURIComponent(e).replace("%40", "@")}` }, e)));
  add("Phone", c.phones.map((p) => h("a", { href: `tel:${p.replace(/[^\d+]/g, "")}` }, p)));
  add("Address", c.addresses);
  add("Social", c.social.map((s) => link(`${s.network}: ${s.url}`, s.url)));
  if (c.contact_page) add("Contact page", [link(c.contact_page, c.contact_page)]);

  return block("Contact", "Emails, phone numbers, addresses and social links.",
    rows.length ? h("dl", { class: "kv" }, rows) : empty("No contact details found on the pages that were read."));
}

function renderFooter(data) {
  return h("section", { class: "block" },
    h("div", {}, h("h2", {}, "Scan details"), h("p", { class: "hint" }, "Pages this scan opened.")),
    h("div", {},
      h("p", { class: "empty" }, `Read ${data.pages_read.length} page(s).`),
      data.warnings.map((w) => h("p", { class: "warn" }, w))));
}

/* ---------- events ---------- */

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = urlInput.value.trim();
  if (!url) {
    setStatus("Enter a website address to scan.", true);
    urlInput.focus();
    return;
  }

  scanBtn.disabled = true;
  scanBtn.textContent = "Scanning...";
  bar.hidden = false;
  results.hidden = true;
  setStatus("Reading the site. This can take up to a minute.");

  try {
    const response = await fetch("/api/scrape", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, max_pages: Number(pagesSelect.value) }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "The scan failed.");

    lastResult = data;
    output.replaceChildren(
      renderOverview(data), renderPages(data), renderServices(data),
      renderContact(data), renderFooter(data));
    results.hidden = false;
    setStatus(`Finished scanning ${data.site.name}.`);
  } catch (err) {
    setStatus(err.message || "Could not reach the scraper. Is app.py running?", true);
  } finally {
    scanBtn.disabled = false;
    scanBtn.textContent = "Scan site";
    bar.hidden = true;
  }
});

document.querySelector("#copy-btn").addEventListener("click", async () => {
  if (!lastResult) return;
  try {
    await navigator.clipboard.writeText(JSON.stringify(lastResult, null, 2));
    setStatus("Copied JSON to the clipboard.");
  } catch {
    setStatus("Could not copy. Use Download JSON instead.", true);
  }
});

document.querySelector("#download-btn").addEventListener("click", () => {
  if (!lastResult) return;
  const blob = new Blob([JSON.stringify(lastResult, null, 2)], { type: "application/json" });
  const a = h("a", { href: URL.createObjectURL(blob), download: "site-survey.json" });
  a.click();
  URL.revokeObjectURL(a.href);
});
