"""One-shot Craigslist search and Zillow listing fetch. No stored corpus."""

from __future__ import annotations

import asyncio
import html
import re
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

CRAIGSLIST_SEARCH = "https://sfbay.craigslist.org/search/sfc/apa"
ZILLOW_SEARCH = "https://www.zillow.com/san-francisco-ca/rentals/"
ZILLOW_BLOCK = (
    "Zillow search returned a block page. Paste a Zillow listing URL to analyze one home."
)
MAX_LISTINGS = 8


class ListingError(Exception):
    pass


def _clean(value: str) -> str:
    value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</p>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    return value.strip()


def _meta(page: str, prop: str) -> str:
    pattern = rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]*)"'
    match = re.search(pattern, page, re.I)
    if not match:
        pattern = rf'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="{re.escape(prop)}"'
        match = re.search(pattern, page, re.I)
    return html.unescape(match.group(1)).strip() if match else ""


def _price_number(label: str) -> int | None:
    match = re.search(r"([0-9][0-9,]*)", label or "")
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def assert_listing_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise ListingError("Paste a Craigslist or Zillow listing URL.")
    if host in {"localhost", "127.0.0.1", "0.0.0.0"} or host.endswith(".local"):
        raise ListingError("Paste a Craigslist or Zillow listing URL.")
    allowed = host.endswith(".craigslist.org") or host == "craigslist.org" or host.endswith(".zillow.com") or host == "zillow.com"
    if not allowed:
        raise ListingError("Paste a Craigslist or Zillow listing URL.")
    return parsed.geturl()


def _blocked(page: str, status: int) -> bool:
    sample = page[:4000].lower()
    if status in {401, 403, 429, 503}:
        return True
    markers = ("px-captcha", "access to this page has been denied", "captcha", "perimeterx", "blocked")
    return any(marker in sample for marker in markers)


def parse_craigslist_search(page: str) -> list[dict[str, Any]]:
    listings = []
    for block in re.findall(r'<li class="cl-static-search-result".*?</li>', page, re.S):
        href = re.search(r'href="([^"]+)"', block)
        title = re.search(r'<div class="title">(.*?)</div>', block, re.S)
        price = re.search(r'<div class="price">(.*?)</div>', block, re.S)
        location = re.search(r'<div class="location">(.*?)</div>', block, re.S)
        if not href or not title:
            continue
        url = html.unescape(href.group(1)).strip()
        price_label = _clean(price.group(1)) if price else ""
        listings.append(
            {
                "id": "",
                "source": "craigslist",
                "title": _clean(title.group(1)),
                "price_label": price_label,
                "price": _price_number(price_label),
                "neighborhood": _clean(location.group(1)) if location else "",
                "location": "",
                "description": "",
                "image_url": "",
                "url": url,
                "reason": "",
                "score": None,
            }
        )
    return listings


def parse_craigslist_listing(page: str, url: str) -> dict[str, Any]:
    if _blocked(page, 200) and "postingbody" not in page and "postingtitle" not in page:
        raise ListingError("Craigslist returned a block page for that listing.")
    title_match = re.search(r'<span id="titletextonly">(.*?)</span>', page, re.S)
    title = _clean(title_match.group(1)) if title_match else _meta(page, "og:title")
    hood_match = re.search(
        r'id="titletextonly">.*?</span>\s*<span>\s*\(([^)]+)\)',
        page,
        re.S,
    )
    price_match = re.search(r'<span class="price">\s*([^<]+)', page, re.S)
    price_label = _clean(price_match.group(1)) if price_match else ""
    address_match = re.search(r'<div class="mapaddress">(.*?)</div>', page, re.S)
    address = _clean(address_match.group(1)) if address_match else ""
    housing_match = re.search(r'<span class="housing">(.*?)</span>', page, re.S)
    housing = _clean(housing_match.group(1)) if housing_match else ""
    housing = housing.replace("ft 2", "ft²").strip(" /-|")
    body_match = re.search(r'<section id="postingbody">(.*?)</section>', page, re.S)
    description = _clean(body_match.group(1)) if body_match else _meta(page, "og:description")
    description = re.sub(r"QR Code Link to This Post", "", description).strip()
    image = _best_craigslist_image(page)
    neighborhood = _clean(hood_match.group(1)) if hood_match else ""
    locality = ""
    city_match = re.search(r'"addressLocality"\s*:\s*"([^"]+)"', page)
    if city_match:
        locality = city_match.group(1)
    location_bits = [bit for bit in (neighborhood, locality or "San Francisco", address, housing, price_label) if bit]
    return {
        "id": "",
        "source": "craigslist",
        "title": title or "Craigslist listing",
        "price_label": price_label,
        "price": _price_number(price_label),
        "neighborhood": neighborhood or locality,
        "location": " · ".join(location_bits),
        "description": description[:2000],
        "image_url": image,
        "url": url,
        "reason": "",
        "score": None,
    }


def _best_craigslist_image(page: str) -> str:
    candidates = re.findall(r"https://images\.craigslist\.org/[^\"'\s]+", page)
    for candidate in candidates:
        if "600x450" in candidate:
            return candidate.split("?")[0]
    og = _meta(page, "og:image")
    if og:
        return html.unescape(og)
    return candidates[0].split("?")[0] if candidates else ""


def parse_zillow_listing(page: str, url: str) -> dict[str, Any]:
    if _blocked(page, 200) and "ld+json" not in page and "og:image" not in page:
        raise ListingError("Zillow returned a block page for that URL. Craigslist search still works.")
    title = _meta(page, "og:title") or _meta(page, "twitter:title")
    description = _meta(page, "og:description") or _meta(page, "description")
    image = _meta(page, "og:image")
    price_label = ""
    price_meta = _meta(page, "product:price:amount")
    neighborhood = ""
    address = ""
    for raw in re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        page,
        re.S,
    ):
        blob = html.unescape(raw)
        if not title:
            name = re.search(r'"name"\s*:\s*"([^"]+)"', blob)
            if name:
                title = html.unescape(name.group(1))
        if not description:
            desc = re.search(r'"description"\s*:\s*"([^"]+)"', blob)
            if desc:
                description = html.unescape(desc.group(1))
        if not image:
            img = re.search(r'"image"\s*:\s*"(https:[^"]+)"', blob)
            if img:
                image = html.unescape(img.group(1))
        if not price_label:
            offer = re.search(r'"price"\s*:\s*"?([0-9][0-9,]*)"?', blob)
            if offer:
                price_label = f"${int(offer.group(1).replace(',', '')):,}"
        street = re.search(r'"streetAddress"\s*:\s*"([^"]*)"', blob)
        city = re.search(r'"addressLocality"\s*:\s*"([^"]*)"', blob)
        if street and street.group(1):
            address = html.unescape(street.group(1))
        if city and city.group(1):
            neighborhood = html.unescape(city.group(1))
    if not price_label and price_meta:
        price_label = f"${_price_number(price_meta):,}" if _price_number(price_meta) else price_meta
    if not title and not description and not image:
        raise ListingError("Could not read that Zillow page. If it was a captcha, paste the URL again in a moment or use Craigslist search.")
    location_bits = [bit for bit in (neighborhood, address, price_label) if bit]
    return {
        "id": "",
        "source": "zillow",
        "title": _clean(title) or "Zillow listing",
        "price_label": price_label,
        "price": _price_number(price_label),
        "neighborhood": neighborhood or "San Francisco",
        "location": " · ".join(location_bits) or "San Francisco",
        "description": _clean(description)[:2000],
        "image_url": image,
        "url": url,
        "reason": "",
        "score": None,
    }


def _assign_ids(listings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    kept = []
    for index, listing in enumerate(listings, start=1):
        url = listing.get("url") or ""
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        listing["id"] = f"{listing.get('source', 'listing')}-{index}"
        kept.append(listing)
    return kept


def _prefer(listings: list[dict[str, Any]], neighborhood: str, max_rent: int) -> list[dict[str, Any]]:
    token = neighborhood.strip().lower()
    affordable = []
    for listing in listings:
        price = listing.get("price")
        if price is not None and price > max_rent:
            continue
        affordable.append(listing)
    pool = affordable or listings
    if token in {"", "sf", "san francisco", "san francisco, ca"}:
        return pool[:MAX_LISTINGS]
    hits = [
        listing
        for listing in pool
        if token in (listing.get("neighborhood") or "").lower()
        or token in (listing.get("title") or "").lower()
    ]
    rest = [listing for listing in pool if listing not in hits]
    return (hits + rest)[:MAX_LISTINGS]


def craigslist_search_url(max_rent: int, neighborhood: str) -> str:
    params = {"hasPic": 1, "max_price": max(1, int(max_rent))}
    query = neighborhood.strip()
    if query.lower() not in {"", "sf", "san francisco", "san francisco, ca"}:
        params["query"] = query
    return f"{CRAIGSLIST_SEARCH}?{urlencode(params)}"


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    return await client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)


async def fetch_listing(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    safe = assert_listing_url(url)
    response = await _get(client, safe)
    page = response.text
    host = _host(str(response.url))
    if host.endswith("zillow.com"):
        if _blocked(page, response.status_code):
            raise ListingError("Zillow returned a block page for that URL. Craigslist search still works.")
        return parse_zillow_listing(page, str(response.url))
    if _blocked(page, response.status_code) and "postingtitle" not in page and "cl-static-search-result" not in page:
        raise ListingError("Craigslist returned a block page.")
    if "cl-static-search-result" in page and "postingbody" not in page:
        found = parse_craigslist_search(page)
        if not found:
            raise ListingError("That Craigslist page had no listings.")
        # A pasted search URL: return the first card and let the caller expand.
        found[0]["url"] = found[0]["url"]
        return found[0]
    return parse_craigslist_listing(page, str(response.url))


async def _fill_craigslist(client: httpx.AsyncClient, listing: dict[str, Any]) -> dict[str, Any]:
    try:
        detailed = await fetch_listing(client, listing["url"])
    except (ListingError, httpx.HTTPError):
        listing["location"] = listing.get("neighborhood") or "San Francisco"
        return listing
    listing.update({key: value for key, value in detailed.items() if value or key == "price"})
    if not listing.get("location"):
        listing["location"] = " · ".join(
            bit for bit in (listing.get("neighborhood"), "San Francisco", listing.get("price_label")) if bit
        )
    return listing


async def search_craigslist(
    client: httpx.AsyncClient, max_rent: int, neighborhood: str
) -> list[dict[str, Any]]:
    url = craigslist_search_url(max_rent, neighborhood)
    response = await _get(client, url)
    if response.status_code >= 400 or (
        _blocked(response.text, response.status_code) and "cl-static-search-result" not in response.text
    ):
        raise ListingError(f"Craigslist search failed ({response.status_code}).")
    cards = parse_craigslist_search(response.text)
    chosen = _prefer(cards, neighborhood, max_rent)
    if not chosen:
        raise ListingError("Craigslist returned no listings for that rent cap.")
    sem = asyncio.Semaphore(4)

    async def one(card: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            return await _fill_craigslist(client, card)

    detailed = await asyncio.gather(*[one(card) for card in chosen])
    return _assign_ids(list(detailed))


def _zillow_cards(page: str) -> list[dict[str, Any]]:
    listings = []
    seen = set()
    for match in re.finditer(r"https://www\.zillow\.com/homedetails/[^\"'\s<]+", page):
        url = html.unescape(match.group(0)).rstrip("\\")
        if url in seen:
            continue
        seen.add(url)
        slug = urlparse(url).path.rstrip("/").split("/")[-2] if "/homedetails/" in url else "Zillow home"
        title = slug.replace("-", " ")
        listings.append(
            {
                "id": "",
                "source": "zillow",
                "title": title,
                "price_label": "",
                "price": None,
                "neighborhood": "San Francisco",
                "location": "San Francisco",
                "description": "",
                "image_url": "",
                "url": url,
                "reason": "",
                "score": None,
            }
        )
        if len(listings) >= MAX_LISTINGS:
            break
    return listings


async def search_zillow(client: httpx.AsyncClient, max_rent: int) -> tuple[list[dict[str, Any]], str]:
    """Best-effort. A captcha is a status string, not a crash."""
    try:
        response = await _get(client, ZILLOW_SEARCH)
    except httpx.HTTPError as exc:
        return [], f"Zillow search could not be reached ({exc.__class__.__name__}). Paste a listing URL."
    if _blocked(response.text, response.status_code) or "homedetails" not in response.text:
        return [], ZILLOW_BLOCK
    cards = _zillow_cards(response.text)[: max(0, MAX_LISTINGS)]
    if not cards:
        return [], "Zillow search returned a page without listing links. Paste a Zillow listing URL."
    filled = []
    for card in cards:
        if len(filled) >= 3:
            break
        try:
            detailed = await fetch_listing(client, card["url"])
        except (ListingError, httpx.HTTPError):
            continue
        if detailed.get("price") and detailed["price"] > max_rent:
            continue
        filled.append(detailed)
    if not filled:
        return [], ZILLOW_BLOCK
    return filled, "Zillow search returned listings."


async def gather_listings(
    max_rent: int,
    neighborhood: str,
    listing_url: str,
) -> tuple[list[dict[str, Any]], str]:
    async with httpx.AsyncClient() as client:
        if listing_url.strip():
            safe = assert_listing_url(listing_url)
            page = await _get(client, safe)
            if "cl-static-search-result" in page.text and "postingbody" not in page.text:
                cards = _prefer(parse_craigslist_search(page.text), neighborhood, max_rent)
                sem = asyncio.Semaphore(4)

                async def one(card: dict[str, Any]) -> dict[str, Any]:
                    async with sem:
                        return await _fill_craigslist(client, card)

                detailed = await asyncio.gather(*[one(card) for card in cards])
                return _assign_ids(list(detailed)), "Pasted Craigslist results. Zillow search was not run."
            listing = await fetch_listing(client, safe)
            return _assign_ids([listing]), "Analyzed the pasted listing URL. Zillow search was not run."

        craigslist = await search_craigslist(client, max_rent, neighborhood)
        zillow, status = await search_zillow(client, max_rent)
        merged = _assign_ids((craigslist + zillow)[:MAX_LISTINGS])
        return merged, status
