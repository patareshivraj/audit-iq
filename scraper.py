"""
scraper.py — Screener.in scraper for fetching annual report PDFs.

Connects to Screener.in, searches for companies, extracts annual report links,
and downloads PDFs. No database or AI coupling — pure scraping logic.
"""

import os
import time
import random
import logging
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)

BASE_URL    = config.SCREENER_BASE_URL
REPORTS_DIR = str(config.REPORTS_DIR)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.screener.in/",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class ScraperError(Exception):
    """Raised when scraping fails."""


class CompanyNotFoundError(ScraperError):
    """Raised when the company is not found on Screener."""


def _sanitize_filename(name: str) -> str:
    """Remove characters that are unsafe in filenames."""
    keep = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._-()")
    return "".join(c if c in keep else "_" for c in name).strip()


class ScreenerScraper:
    """Scrapes Screener.in for company annual reports."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def search_company(self, query: str) -> dict:
        """
        Search Screener.in for a company.

        Returns:
            {"path": "/company/TCS/consolidated/", "name": "Tata Consultancy Services Ltd."}

        Raises:
            CompanyNotFoundError: if nothing found.
            ScraperError: on network/parse error.
        """
        url = f"{BASE_URL}/api/company/search/?q={quote(query.strip())}"
        logger.info("Searching Screener for: '%s'", query)

        try:
            resp = self.session.get(url, timeout=config.SCRAPER_TIMEOUT)
        except requests.RequestException as exc:
            raise ScraperError(f"Network error searching for '{query}': {exc}") from exc

        if resp.status_code != 200:
            raise ScraperError(
                f"Screener search returned HTTP {resp.status_code} for '{query}'"
            )

        try:
            results = resp.json()
        except ValueError as exc:
            raise ScraperError(f"Screener returned non-JSON response: {exc}") from exc

        if not results:
            raise CompanyNotFoundError(
                f"No company found for '{query}' on Screener.in"
            )

        company = {"path": results[0]["url"], "name": results[0]["name"]}
        logger.info("Found: %s → %s", company["name"], company["path"])
        return company

    def get_annual_report_links(self, company_path: str) -> list[dict]:
        """
        Scrape the company page for annual report PDF links.

        Returns:
            [{"title": "Annual Report 2024", "url": "https://..."}, ...]
        """
        target_url = urljoin(BASE_URL, company_path)
        logger.info("Loading company page: %s", target_url)

        time.sleep(random.uniform(0.5, 1.5))  # polite delay

        try:
            resp = self.session.get(target_url, timeout=config.SCRAPER_TIMEOUT)
        except requests.RequestException as exc:
            raise ScraperError(f"Could not load company page: {exc}") from exc

        if resp.status_code != 200:
            raise ScraperError(f"Company page returned HTTP {resp.status_code}")

        soup = BeautifulSoup(resp.text, "html.parser")

        # ── 3-layer resilient extraction ─────────────────────────────────────
        docs_section = soup.select_one("section#documents")
        if not docs_section:
            logger.warning("No #documents section — Screener layout may have changed")
            return []

        annual_div = docs_section.select_one("div.documents.annual-reports")
        if not annual_div:
            heading = docs_section.find(
                lambda tag: tag.name == "h3" and "Annual" in tag.get_text()
            )
            annual_div = heading.parent if heading else docs_section

        links = annual_div.select("ul.list-links li a") or annual_div.find_all("a")

        reports = []
        for link in links:
            title = link.get_text(strip=True)
            href  = link.get("href", "")
            if not href:
                continue

            if (
                ".pdf" in href.lower()
                or "annualreport" in href.lower()
                or "annual" in title.lower()
            ):
                # Normalise URL
                if href.startswith("//"):
                    href = "https:" + href
                elif not href.startswith("http"):
                    href = urljoin(BASE_URL, href)

                reports.append({"title": title or f"Report_{len(reports)+1}", "url": href})
                if len(reports) >= config.MAX_REPORTS:
                    break

        logger.info("Found %d annual report link(s)", len(reports))
        return reports

    def download_pdf(self, url: str, company_name: str, title: str = "report") -> str:
        """
        Download a PDF and save it locally.

        Returns:
            Local file path of the downloaded PDF.
        """
        safe_company = _sanitize_filename(company_name).replace(" ", "_")
        save_dir = Path(REPORTS_DIR) / safe_company
        save_dir.mkdir(parents=True, exist_ok=True)

        safe_title = _sanitize_filename(title)
        if not safe_title.lower().endswith(".pdf"):
            safe_title += ".pdf"

        file_path = save_dir / safe_title

        # Cache hit — skip download
        if file_path.exists() and file_path.stat().st_size > 1_000:
            logger.info("PDF already cached: %s", file_path)
            return str(file_path)

        logger.info("Downloading: %s", url)
        time.sleep(random.uniform(0.3, 1.0))

        try:
            with self.session.get(
                url, stream=True, timeout=config.DOWNLOAD_TIMEOUT
            ) as resp:
                resp.raise_for_status()

                content_type = resp.headers.get("Content-Type", "")
                if "html" in content_type.lower():
                    raise ScraperError(
                        "URL returned HTML instead of PDF — likely behind a login wall"
                    )

                with open(file_path, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=16_384):
                        fh.write(chunk)

        except requests.RequestException as exc:
            if file_path.exists():
                file_path.unlink(missing_ok=True)
            raise ScraperError(f"Could not download PDF: {exc}") from exc

        size = file_path.stat().st_size
        logger.info("Downloaded: %s (%.1f MB)", file_path, size / 1_048_576)

        if size < 5_000:
            file_path.unlink(missing_ok=True)
            raise ScraperError("Downloaded file too small — likely not a valid PDF")

        return str(file_path)
