import asyncio
import os
import re
import shutil
from html import unescape
from typing import Dict, List, Optional
from urllib.parse import urlparse
import aiohttp
from bs4 import BeautifulSoup

GITHUB_USER = os.getenv("USER_NAME", "username")
GITHUB_REPO = os.getenv("REPO_NAME", "repo")
GITHUB_BRANCH = os.getenv("BRANCH_NAME", "main")

FILE_NAME = "ecanlitvizle-org"
DOMAIN = "ecanlitvizle.live"
BASE_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/refs/heads/{GITHUB_BRANCH}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Referer": "https://www.ecanlitvizle.live/"
}
PATTERN_EMBED = r'"embedUrl": "(.*?)"'

# Aynı anda atılacak istek sınırını kontrol eder (Sunucu tarafından engellenmemek için)
CONCURRENCY_LIMIT = 10 
semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

def decode_video_url(encrypted_string: str) -> Optional[str]:
    delimiter = 'Äx|Xf|x'
    parts = encrypted_string.split(delimiter)
    if len(parts) < 2:
        return None

    try:
        starting_position = int(parts[0])
    except (ValueError, IndexError):
        return None

    encrypted_url = parts[1]
    cipher_alphabet = [
        '€', '$', 'Ă', 'Ä', 'Ë', 'Ģ', 'Ḩ', 'Ķ', 'Ḽ', 'Ņ',
        'Ň', 'Š', 'Ț', 'Ž', 'Ә', 'Є', 'Б', 'Җ', 'Ч', 'Ж',
        'Д', 'Ӡ', 'Ф', 'Ғ', 'Ӷ', 'Ы', 'И', 'К', 'Љ', 'Ө',
        'Ў', 'Њ', 'Һ', 'Г', 'Ş'
    ]
    url_chars = [
        '0', '1', '2', '3', '4', '5', '6', '7', '8', '9',
        '.', '&', '=', 'w', '?', 'c', 'o', 'm', 'a', 'f',
        'l', 'i', 'h', 't', 's', ':', '/', 'r', 'e', 'd',
        'n', 'k', 'p', '_', '-'
    ]

    position = starting_position
    decoded_url = encrypted_url

    for i in range(len(url_chars)):
        if position >= len(cipher_alphabet):
            position = 0
        cipher_char = cipher_alphabet[position]
        url_char = url_chars[i]
        decoded_url = decoded_url.replace(cipher_char, url_char)
        position += 1

    return decoded_url

def extract_file_from_html(html: str) -> Optional[str]:
    html = unescape(html)
    patterns = [
        r"file\s*:\s*['\"]([^'\"]+)['\"]",
        r"file\s*:\s*&#039;([^&#039;]+)&#039;",
        r"file\s*=\s*['\"]([^'\"]+)['\"]",
        r"'file'\s*:\s*['\"]([^'\"]+)['\"]",
        r'"file"\s*:\s*["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None

def extract_quality_options(html: str) -> Dict[str, str]:
    html = unescape(html)
    qualities = {}
    pattern = r'["\']#kalite(\d+)["\'].*?changeVideo\(["\']([^"\']+)["\']\)'
    matches = re.findall(pattern, html, re.DOTALL)
    for match in matches:
        qualities[match[0]] = match[1].strip()
    return qualities

def decode_all_qualities(qualities: Dict[str, str]) -> Dict[str, str]:
    decoded_qualities = {}
    for resolution, encoded_url in qualities.items():
        if 'Äx|Xf|x' in encoded_url:
            decoded_url = decode_video_url(encoded_url)
            if decoded_url:
                decoded_qualities[resolution] = decoded_url
        else:
            decoded_qualities[resolution] = encoded_url
    return decoded_qualities

async def fetch_channel_param(session: aiohttp.ClientSession, kanal) -> Dict[str, str]:
    """Kanal detay sayfasına paralel gidip embed parametresini çeker."""
    a_tag = kanal.find("a")
    img_tag = kanal.find("img")
    if not a_tag or not img_tag:
        return None

    link = a_tag.get("href")
    title = a_tag.get("title")
    img = img_tag.get("src")
    param = ""

    async with semaphore:
        try:
            async with session.get(link, headers=HEADERS, timeout=10) as resp:
                text = await resp.text()
                match = re.search(PATTERN_EMBED, text)
                if match:
                    param = match.group(1).replace('\\/', '/').split("=")[-1]
        except Exception as e:
            print(f"Hata ({title}): {e}")

    return {"name": title, "img": img, "param": param}

async def get_ecanlitv(session: aiohttp.ClientSession) -> List[Dict[str, str]]:
    """Tüm kanalları ve sayfaları eşzamanlı olarak çeker."""
    url = "https://www.ecanlitvizle.live/"
    try:
        async with session.get(url, headers=HEADERS, timeout=10) as resp:
            content = await resp.text()
            soup = BeautifulSoup(content, "html.parser")
    except Exception as e:
        print(f"Ana sayfa hatası: {e}")
        return []

    tasks = []
    kanal_liste = soup.find("ul", class_="kanallar")
    if kanal_liste:
        for kanal in kanal_liste.find_all("li"):
            tasks.append(fetch_channel_param(session, kanal))

    nav = soup.find("div", attrs={"id": "navigation"})
    if nav:
        for page in nav.find_all("a"):
            page_link = page.get("href")
            try:
                async with session.get(page_link, headers=HEADERS, timeout=10) as p_resp:
                    p_content = await p_resp.text()
                    p_soup = BeautifulSoup(p_content, "html.parser")
                    p_list = p_soup.find("ul", class_="kanallar")
                    if p_list:
                        for kanal in p_list.find_all("li"):
                            tasks.append(fetch_channel_param(session, kanal))
            except Exception as e:
                print(f"Sayfa hatası ({page_link}): {e}")

    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]

async def get_stream_urls(session: aiohttp.ClientSession, param: str, yayin: int = 1) -> Optional[List[str]]:
    if yayin > 3 or not param:
        return None

    url = f"https://www.ecanlitvizle.live//embed.php?kanal={param}&yayin={yayin}"
    async with semaphore:
        try:
            async with session.get(url, headers=HEADERS, timeout=10) as resp:
                html_content = await resp.text()
                streams = extract_quality_options(html_content)
                decoded_streams = list(decode_all_qualities(streams).values())

                if not streams:
                    stream = extract_file_from_html(html_content)
                    if stream:
                        decoded_stream = decode_video_url(stream) if 'Äx|Xf|x' in stream else stream
                        decoded_streams.append(decoded_stream)

                if decoded_streams and decoded_streams[0] and DOMAIN in decoded_streams[0]:
                    return decoded_streams
                else:
                    return await get_stream_urls(session, param, yayin + 1)
        except Exception as e:
            print(f"Yayın hatası ({param}): {e}")
            return None

async def save_file(session: aiohttp.ClientSession, path: str, streams: List[str]) -> bool:
    try:
        if len(streams) == 1:
            url = streams[0]
            async with session.get(url, headers=HEADERS, timeout=10) as resp:
                if resp.status != 200:
                    return False
                text = await resp.text()

            with open(path, "w", encoding="utf-8") as f:
                if "EXT-X-STREAM-INF" in text:
                    base_url = url.rsplit("/", 1)[0]
                    for line in text.splitlines():
                        if not line.startswith("#") and "http" not in line:
                            line = f"{base_url}/{line}"
                        f.write(f"{line}\n")
                else:
                    f.write("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=800000\n")
                    f.write(f"{url}\n")

        elif len(streams) > 1:
            bw_list = ["800000", "1200000", "1800000", "2500000", "3000000"]
            with open(path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for i, url in enumerate(streams):
                    bw = bw_list[i] if i < len(bw_list) else "3000000"
                    f.write(f"#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH={bw}\n{url}\n")
        return True
    except Exception as e:
        print(f"Dosya kaydetme hatası: {e}")
        return False

async def process_channel(session: aiohttp.ClientSession, kanal: Dict[str, str], playlist_lines: List[str]):
    if not kanal["param"]:
        return

    stream_urls = await get_stream_urls(session, kanal['param'])
    if stream_urls:
        channel_slug = urlparse(stream_urls[0]).path.split('/')[-1].split('.')[0].replace("-master", "")
        file_name = f"{channel_slug}.m3u8"
        file_path = os.path.join(FILE_NAME, file_name)

        if await save_file(session, file_path, stream_urls):
            github_url = f"{BASE_URL}/{FILE_NAME}/{file_name}"
            playlist_lines.append(f'#EXTINF:-1 tvg-id="" tvg-name="{kanal["name"]}" tvg-logo="{kanal["img"]}",{kanal["name"]}\n')
            playlist_lines.append(f"#EXTVLCOPT:http-user-agent={HEADERS['User-Agent']}\n")
            playlist_lines.append(f"{github_url}\n")

async def main():
    async with aiohttp.ClientSession() as session:
        kanallar = await get_ecanlitv(session)
        print(f"Toplam {len(kanallar)} kanal bulundu. İşleniyor...")

        shutil.rmtree(FILE_NAME, ignore_errors=True)
        os.makedirs(FILE_NAME, exist_ok=True)
        os.makedirs("playlists", exist_ok=True)

        playlist_lines = ["#EXTM3U\n"]
        tasks = [process_channel(session, kanal, playlist_lines) for kanal in kanallar]
        await asyncio.gather(*tasks)

        playlist_file_path = os.path.join("playlists", f"{FILE_NAME}.m3u")
        with open(playlist_file_path, "w", encoding="utf-8") as f:
            f.writelines(playlist_lines)

        print("İşlem tamamlandı!")

if __name__ == "__main__":
    asyncio.run(main())
