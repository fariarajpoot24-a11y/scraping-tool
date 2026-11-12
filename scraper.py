"""Scraping logic: finds pages, services, purpose and contact info for a website."""

import ipaddress
import json
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

USER_AGENT = "SiteSurveyBot/1.0 (personal research tool)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
TIMEOUT = 10
MAX_BYTES = 2_000_000
MAX_LINKS = 80

SKIP_EXT = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".zip", ".rar",
    ".mp3", ".mp4", ".avi", ".mov", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".css", ".js", ".xml", ".json",
)

# Order matters: the first category that matches wins.
CATEGORY_KEYWORDS = {
    "contact": ("contact", "reach-us", "get-in-touch", "find-us", "location", "support"),
    "about": ("about", "who-we-are", "our-story", "company", "mission", "team"),
    "services": ("service", "solution", "what-we-do", "offering", "product", "treatment",
                 "program", "course", "pricing"),
}

SERVICE_HEADING_RE = re.compile(
    r"\b(services?|what we do|solutions?|offerings?|products?|what we offer|"
    r"specialt(?:y|ies)|expertise|capabilities)\b", re.I)
GENERIC_LABELS = {
    "services", "our services", "all services", "products", "our products", "solutions",
    "our solutions", "learn more", "read more", "view all", "see all", "contact us",
    "get started", "get a quote", "home", "about us", "about", "contact", "what we do",
    "what we offer", "pricing", "blog", "menu", "search", "login", "sign in", "sign up",
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w/])(\+?\(?\d[\d\s().-]{7,18}\d)(?![\w/])")
BAD_EMAIL_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")
BAD_EMAIL_DOMAINS = ("example.com", "domain.com", "sentry.io", "yourdomain.com")

SOCIAL_DOMAINS = {
    "facebook.com": "Facebook", "instagram.com": "Instagram", "linkedin.com": "LinkedIn",
    "twitter.com": "X (Twitter)", "x.com": "X (Twitter)", "youtube.com": "YouTube",
    "tiktok.com": "TikTok", "github.com": "GitHub", "wa.me": "WhatsApp",
    "pinterest.com": "Pinterest",
}


class ScrapeError(Exception):
    """A problem we can explain to the user."""


# ---------------------------------------------------------------- safety / fetching

def normalize_url(raw):
    raw = (raw or "").strip()
    if not raw:
        raise ScrapeError("Enter a website address.")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ScrapeError("Enter a valid web address that starts with http:// or https://.")
    return urldefrag(raw)[0]


def assert_public(url):
    """Refuse local/private addresses so the tool can't be pointed at your own network."""
    host = urlparse(url).hostname
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ScrapeError(f"Could not find a website at '{host}'. Check the spelling.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise ScrapeError("That address points to a private or local network, so it was blocked.")


def fetch_html(url):
    """Download one HTML page (following up to 5 redirects). Returns (final_url, bytes)."""
    for _ in range(5):
        assert_public(url)
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=False)
        try:
            if r.status_code in (301, 302, 303, 307, 308):
                location = r.headers.get("Location")
                if not location:
                    raise ScrapeError("The site sent a redirect without a destination.")
                url = urljoin(url, location)
                continue
            r.raise_for_status()
            if "html" not in r.headers.get("Content-Type", "").lower():
                raise ScrapeError("That address is not an HTML web page.")
            chunks, size = [], 0
            for chunk in r.iter_content(65536):
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    break
            return url, b"".join(chunks)
        finally:
            r.close()
    raise ScrapeError("The site redirected too many times.")


class Robots:
    """Minimal robots.txt check. If robots.txt can't be read, everything is allowed."""

    def __init__(self, url):
        self.parser = RobotFileParser()
        self.loaded = False
        p = urlparse(url)
        try:
            r = requests.get(f"{p.scheme}://{p.netloc}/robots.txt", headers=HEADERS, timeout=5)
            if r.status_code == 200:
                self.parser.parse(r.text.splitlines())
                self.loaded = True
        except requests.RequestException:
            pass

    def allowed(self, url):
        return (not self.loaded) or self.parser.can_fetch(USER_AGENT, url)


# ---------------------------------------------------------------- parsing helpers

def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def host_key(host):
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def categorize(url, label):
    text = (urlparse(url).path + " " + label).lower()
    for category, words in CATEGORY_KEYWORDS.items():
        if any(w in text for w in words):
            return category
    return "other"


def meta(soup, key):
    tag = soup.find("meta", attrs={"name": key}) or soup.find("meta", attrs={"property": key})
    return clean(tag.get("content")) if tag and tag.get("content") else ""


def jsonld_items(soup):
    items = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except ValueError:
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                items.append(x)
                if "@graph" in x:
                    stack.append(x["@graph"])
    return items


def discover_links(soup, base_url):
    site = host_key(urlparse(base_url).hostname)
    anchors = list(soup.select("nav a[href], header a[href]")) + soup.find_all("a", href=True)
    links = {}
    for a in anchors:
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "sms:")):
            continue
        full = urldefrag(urljoin(base_url, href))[0]
        p = urlparse(full)
        if p.scheme not in ("http", "https") or host_key(p.hostname) != site:
            continue
        if p.path.lower().endswith(SKIP_EXT):
            continue
        label = clean(a.get_text(" ")) or clean(a.get("title")) or clean(a.get("aria-label"))
        key = full.rstrip("/")
        if key in links:
            if not links[key]["label"] and label:
                links[key]["label"] = label[:80]
            continue
        links[key] = {"label": label[:80], "url": full}
        if len(links) >= MAX_LINKS:
            break
    result = []
    for item in links.values():
        if not item["label"]:
            slug = urlparse(item["url"]).path.strip("/").split("/")[-1]
            item["label"] = slug.replace("-", " ").replace("_", " ").title() or "Home"
        item["category"] = categorize(item["url"], item["label"])
        result.append(item)
    return result


def looks_like_item(text):
    words = text.split()
    return (3 <= len(text) <= 80 and len(words) <= 10 and text.lower() not in GENERIC_LABELS
            and not text.lower().startswith(("learn more", "read more", "get a quote", "view ")))


def service_items_under(heading):
    level = int(heading.name[1])
    found = []

    def collect(elements):
        for el in elements:
            text = clean(el.get_text(" "))
            if looks_like_item(text):
                found.append(text)

    for sib in heading.find_next_siblings():
        if sib.name and re.fullmatch(r"h[1-6]", sib.name) and int(sib.name[1]) <= level:
            break
        if sib.name in ("li", "h3", "h4", "h5"):
            collect([sib])
        if sib.name:
            collect(sib.find_all(["li", "h3", "h4", "h5"]))
    if not found and heading.parent:
        collect([e for e in heading.parent.find_all(["li", "h3", "h4", "h5"]) if e is not heading])
    return found


def extract_services(soup):
    found = []
    for heading in soup.find_all(["h1", "h2", "h3"]):
        text = clean(heading.get_text(" "))
        if len(text) <= 60 and SERVICE_HEADING_RE.search(text):
            found.extend(service_items_under(heading))
    return found


def extract_phones(text):
    phones = []
    for match in PHONE_RE.findall(text):
        digits = re.sub(r"\D", "", match)
        if not 9 <= len(digits) <= 15:
            continue
        if re.fullmatch(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}", match.strip()):
            continue
        if "+" in match or match.startswith("0") or re.search(r"[\s().-]", match):
            phones.append(clean(match))
    return phones


def ld_contact(items):
    emails, phones, addresses = [], [], []
    for item in items:
        nodes = [item]
        cp = item.get("contactPoint")
        nodes += cp if isinstance(cp, list) else ([cp] if isinstance(cp, dict) else [])
        for node in nodes:
            if isinstance(node.get("email"), str):
                emails.append(node["email"].replace("mailto:", ""))
            if isinstance(node.get("telephone"), str):
                phones.append(clean(node["telephone"]))
        addr = item.get("address")
        if isinstance(addr, str):
            addresses.append(clean(addr))
        elif isinstance(addr, dict):
            keys = ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry")
            parts = [clean(addr[k]) for k in keys if isinstance(addr.get(k), str)]
            if parts:
                addresses.append(", ".join(parts))
    return emails, phones, addresses


def first_paragraph(soup, min_len=60, max_len=500):
    container = soup.find("main") or soup
    for p in container.find_all("p"):
        text = clean(p.get_text(" "))
        if min_len <= len(text) <= max_len:
            return text
    return ""


def analyze(url, content):
    """Parse one page into a dict of everything we care about."""
    soup = BeautifulSoup(content, "html.parser")
    ld = jsonld_items(soup)
    ld_emails, ld_phones, ld_addresses = ld_contact(ld)

    org_name = ""
    for item in ld:
        kind = str(item.get("@type", ""))
        if item.get("name") and any(k in kind for k in ("Organization", "Business", "WebSite", "Store")):
            org_name = clean(str(item["name"]))
            break
    ld_desc = next((clean(i["description"]) for i in ld if isinstance(i.get("description"), str)), "")

    title = clean(soup.title.get_text()) if soup.title else ""
    h1 = soup.find("h1")

    links = discover_links(soup, url)
    emails, phones, addresses, socials = list(ld_emails), list(ld_phones), list(ld_addresses), []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        low = href.lower()
        if low.startswith("mailto:"):
            emails.append(unquote(href[7:].split("?")[0]))
        elif low.startswith("tel:"):
            phones.append(clean(unquote(href[4:])))
        elif low.startswith("http"):
            p = urlparse(href)
            host = host_key(p.hostname)
            if host in SOCIAL_DOMAINS and not re.search(r"share|intent|sharer", p.path + p.query, re.I) \
                    and p.path.strip("/"):
                socials.append({"network": SOCIAL_DOMAINS[host], "url": href})

    for tag in soup.find_all("address"):
        text = clean(tag.get_text(", "))
        if 5 <= len(text) <= 200:
            addresses.append(text)

    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = soup.get_text(" ")
    emails += EMAIL_RE.findall(text)
    phones += extract_phones(text)

    return {
        "url": url,
        "title": title,
        "site_name": meta(soup, "og:site_name") or org_name,
        "description": meta(soup, "description") or meta(soup, "og:description") or ld_desc,
        "tagline": clean(h1.get_text(" "))[:160] if h1 else "",
        "links": links,
        "services": extract_services(soup),
        "headings": [clean(h.get_text(" ")) for h in soup.find_all(["h2", "h3"])],
        "paragraph": first_paragraph(soup),
        "emails": emails,
        "phones": phones,
        "addresses": addresses,
        "socials": socials,
    }


def unique(values, key=lambda v: v):
    seen, out = set(), []
    for v in values:
        k = key(v)
        if k and k not in seen:
            seen.add(k)
            out.append(v)
    return out


def valid_email(email):
    e = email.lower().strip(".")
    return not e.endswith(BAD_EMAIL_SUFFIX) and e.split("@")[-1] not in BAD_EMAIL_DOMAINS


# ---------------------------------------------------------------- main entry point

def scrape(raw_url, max_pages=6):
    url = normalize_url(raw_url)
    assert_public(url)

    robots = Robots(url)
    if not robots.allowed(url):
        raise ScrapeError("This site's robots.txt asks scrapers not to read this page.")

    home_url, content = fetch_html(url)
    if host_key(urlparse(home_url).hostname) != host_key(urlparse(url).hostname):
        robots = Robots(home_url)
    home = analyze(home_url, content)

    # Choose which internal pages to read: contact, about and services first.
    order = {"contact": 0, "about": 1, "services": 2, "other": 3}
    candidates = sorted(home["links"], key=lambda l: order[l["category"]])
    home_key = home_url.rstrip("/")
    to_read = [l for l in candidates
               if l["url"].rstrip("/") != home_key and robots.allowed(l["url"])][: max(0, max_pages - 1)]

    warnings, pages = [], {home_url: home}

    def read(link):
        try:
            final, body = fetch_html(link["url"])
            return link, analyze(final, body), None
        except (ScrapeError, requests.RequestException) as exc:
            return link, None, str(exc)

    if to_read:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for link, info, error in pool.map(read, to_read):
                if info:
                    info["category"] = link["category"]
                    pages[info["url"]] = info
                else:
                    warnings.append(f"Could not read {link['url']}")

    infos = list(pages.values())

    # Pages list (merge links found on every page we read).
    all_links = unique([l for i in infos for l in i["links"]], key=lambda l: l["url"].rstrip("/"))
    all_links = all_links[:MAX_LINKS]

    # Services
    services = []
    for info in infos:
        services += info["services"]
        if info.get("category") == "services" and not info["services"]:
            services += [h for h in info["headings"] if looks_like_item(h)]
    for link in all_links:
        depth = len([s for s in urlparse(link["url"]).path.split("/") if s])
        if link["category"] == "services" and depth >= 2 and looks_like_item(link["label"]):
            services.append(link["label"])
    services = unique(services, key=lambda s: s.lower())[:30]

    # Purpose
    about_text = next((i["paragraph"] for i in infos if i.get("category") == "about" and i["paragraph"]), "")
    if home["description"]:
        summary, source = home["description"], "Site description"
    elif about_text:
        summary, source = about_text, "About page"
    else:
        summary, source = home["paragraph"], "Homepage text"

    # Contact
    emails = unique([e for i in infos for e in i["emails"] if valid_email(e)], key=str.lower)
    phones = unique([p for i in infos for p in i["phones"]], key=lambda p: re.sub(r"\D", "", p))
    addresses = unique([a for i in infos for a in i["addresses"]], key=str.lower)
    socials = unique([s for i in infos for s in i["socials"]], key=lambda s: s["url"].rstrip("/"))
    contact_page = next((l["url"] for l in all_links if l["category"] == "contact"), None)

    return {
        "url": home_url,
        "site": {
            "name": home["site_name"] or home["title"] or urlparse(home_url).hostname,
            "title": home["title"],
            "tagline": home["tagline"],
        },
        "purpose": {"summary": summary, "source": source if summary else "", "about": about_text},
        "pages": all_links,
        "services": services,
        "contact": {
            "emails": emails[:10],
            "phones": phones[:10],
            "addresses": addresses[:5],
            "social": socials[:12],
            "contact_page": contact_page,
        },
        "pages_read": [i["url"] for i in infos],
        "warnings": warnings,
    }
