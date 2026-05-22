# utils/parser.py
from loguru import logger
from bs4 import BeautifulSoup
import re

def html_parser(html_content: str) -> str:
    if not isinstance(html_content, str):
        return ""

    soup = BeautifulSoup(html_content, "html.parser")

    # 🔴 PRESERVE LINE BREAKS
    for br in soup.find_all(["br", "p", "div"]):
        br.insert_after("\n")

    text = soup.get_text()

    # 🔴 KEEP LINE STRUCTURE
    text = re.sub(r"[ \t]+", " ", text)   # only collapse spaces
    text = re.sub(r"\n+", "\n", text)     # normalize newlines

    return text.strip()

