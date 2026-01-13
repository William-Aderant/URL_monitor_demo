"""Fetcher module for retrieving PDFs from URLs."""

from fetcher.aws_web_scraper import AWSWebScraper
from fetcher.pdf_downloader import PDFDownloader

__all__ = [
    "AWSWebScraper",
    "PDFDownloader",
]


