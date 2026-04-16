# utils/parser.py
from loguru import logger
from bs4 import BeautifulSoup
import re

def html_parser(html_content: str) -> str:
    if not isinstance(html_content, str):
        return ""
    text = BeautifulSoup(html_content, "html.parser").get_text(separator=" ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

