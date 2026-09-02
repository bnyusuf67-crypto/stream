import asyncio
import os
import re
import shutil
from html import unescape
from typing import Dict, List, Optional
from urllib.parse import urlparse
import aiohttp

GITHUB_USER = os.getenv("USER_NAME", "bnyusuf67-crypto")
GITHUB_REPO = os.getenv("REPO_NAME", "stream")
GITHUB_BRANCH = os.getenv("BRANCH_NAME", "main")

FILE_NAME = "ecanlitvizle-org"
DOMAIN = "ecanlitvizle.live"
BASE_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/refs/heads/{GITHUB_BRANCH}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Referer": "https://www.ecanlitvizle.live/"
}

CONCURRENCY_LIMIT = 20
semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
MAX_RETRIES = 3

# Esnek Regex Kalıpları (URL Yapılarına Tam Uyumlu)
KANAL_PATTERN = re.compile(
    r'<li>\s*<a\s+href=["\'](?:https?://www\.ecanlitvizle\.live)?/([^"\']+)-canli-izle/?(?:\d+)?["\']\s+title=["\']([^"\']+)["\'].*?<img\s+src=["\']([^"\']+)["\']', 
    re.DOTALL | re.IGNORECASE
)
EMBED_PATTERN = re.compile(r'"embedUrl":\s*"(.*?)"', re.IGNORECASE)
QUALITY_PATTERN = re.compile(r'["\']#kalite(\d+)["\'].*?changeVideo\(["\']([^"\']+)["\']\)', re.DOTALL | re.IGNORECASE)
FILE_PATTERNS = [
    re.compile(r"file\s*:\s*['\"]([^'\"]+)['\"]", re.IGNORECASE),
    re.compile(r"file\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE),
    re.compile(r"file\s*:\s*&#039;([^&#039;]+)&#039;", re.IGNORECASE),
    re.compile(r'https?://[^\s"\']+\.m3u8[^\s"\']*', re.IGNORECASE)
]


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
        decoded_url = decoded_url.replace(cipher_alphabet[position], url_chars[i])
        position += 1

    return decoded_url


async def fetch_page_text(session: aiohttp.ClientSession, url: str) -> str:
    for _ in range(MAX_RETRIES):
        try:
            async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    return await resp.text()
        except Exception:
            await asyncio.sleep(0.3)
    return ""


async def resolve_channel_param(session: aiohttp.ClientSession, slug: str, title: str, img: str) -> Dict[str, str]:
    """Önce slug'ı parametre olarak dener, bulamazsa detay sayfasından 'embedUrl' çeker."""
    clean_slug = slug.strip('/')
    channel_url = f"https://www.ecanlitvizle.live/{clean_slug}-canli-izle/"
    
    # Varsayılan parametre olarak slug'ı ata
    param = clean_slug
    
    # Doğruluğu teyit etmek veya asıl embed parametresini almak için detay sayfasına bak
    async with semaphore:
        html = await fetch_page_text(session, channel_url)
        if html:
            match = EMBED_PATTERN.search(html)
            if match:
                extracted_param = match.group(1).replace('\\/', '/').split("=")[-1]
                if extracted_param:
                    param = extracted_param

    return {"name": title, "img": img, "param": param}


async def get_all_channels(session: aiohttp.ClientSession) -> List[Dict[str, str]]:
    main_url = "https://www.ecanlitvizle.live/"
    html = await fetch_page_text(session, main_url)
    if not html:
        return []

    page_links = list(set(re.findall(r'href=["\']((?:https?://www\.ecanlitvizle\.live)?/sayfa/\d+/?)["\']', html)))
    full_page_links = [p if p.startswith("http") else f"https://www.ecanlitvizle.live{p}" for p in page_links]
    full_page_links.append(main_url)

    pages_html = await asyncio.gather(*[fetch_page_text(session, u) for u in full_page_links])
    
    raw_channels = {}
    for p_html in pages_html:
        for slug, title, img in KANAL_PATTERN.findall(p_html):
            clean_slug = slug.strip('/')
            if clean_slug not in raw_channels:
                raw_channels[clean_slug] = (clean_slug, title, img)

    # Parametreleri doğru tespit et
    results = await asyncio.gather(*[
        resolve_channel_param(session, slug, title, img) 
        for slug, title, img in raw_channels.values()
    ])
    
    return list(results)


async def get_stream_urls(session: aiohttp.ClientSession, param: str) -> Optional[List[str]]:
    """Yayın 1, 2 ve 3 alternatiflerinin hepsini sırayla sorgular."""
    async with semaphore:
        for yayin_no in [1, 2, 3]:
            url = f"https://www.ecanlitvizle.live/embed.php?kanal={param}&yayin={yayin_no}"
            html_content = unescape(await fetch_page_text(session, url))
            if not html_content:
                continue

            qualities = dict(QUALITY_PATTERN.findall(html_content))
            decoded_streams = []

            for enc_url in qualities.values():
                enc_url = enc_url.strip()
                if 'Äx|Xf|x' in enc_url:
                    d_url = decode_video_url(enc_url)
                    if d_url: 
                        decoded_streams.append(d_url)
                else:
                    decoded_streams.append(enc_url)

            if not decoded_streams:
                for pattern in FILE_PATTERNS:
                    match = pattern.search(html_content)
                    if match:
                        stream = match.group(0 if 'http' in pattern.pattern else 1).strip()
                        decoded_stream = decode_video_url(stream) if 'Äx|Xf|x' in stream else stream
                        if decoded_stream:
                            decoded_streams.append(decoded_stream)
                            break

            # Geçerli bir M3U8 linki bulunduysa döndür
            valid_streams = [s for s in decoded_streams if s and ("m3u8" in s or DOMAIN in s)]
            if valid_streams:
                return valid_streams

    return None


async def process_channel(session: aiohttp.ClientSession, kanal: Dict[str, str], playlist_lines: List[str]):
    stream_urls = await get_stream_urls(session, kanal['param'])
    if stream_urls:
        parsed_path = urlparse(stream_urls[0]).path.split('/')[-1]
        channel_slug = parsed_path.split('.')[0].replace("-master", "") if parsed_path else kanal['param']
        
        file_name = f"{channel_slug}.m3u8"
        file_path = os.path.join(FILE_NAME, file_name)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=800000\n")
                f.write(f"{stream_urls[0]}\n")

            github_url = f"{BASE_URL}/{FILE_NAME}/{file_name}"
            playlist_lines.append(f'#EXTINF:-1 tvg-id="" tvg-name="{kanal["name"]}" tvg-logo="{kanal["img"]}",{kanal["name"]}\n')
            playlist_lines.append(f"#EXTVLCOPT:http-user-agent={HEADERS['User-Agent']}\n")
            playlist_lines.append(f"{github_url}\n")
        except Exception:
            pass


async def main():
    connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        kanallar = await get_all_channels(session)

        shutil.rmtree(FILE_NAME, ignore_errors=True)
        os.makedirs(FILE_NAME, exist_ok=True)
        os.makedirs("playlists", exist_ok=True)

        playlist_lines = ["#EXTM3U\n"]
        await asyncio.gather(*[process_channel(session, kanal, playlist_lines) for kanal in kanallar])

        playlist_file_path = os.path.join("playlists", f"{FILE_NAME}.m3u")
        with open(playlist_file_path, "w", encoding="utf-8") as f:
            f.writelines(playlist_lines)

        print(f"İşlem tamamlandı. Toplam eklenen kanal: {len(playlist_lines) // 3}")


if __name__ == "__main__":
    asyncio.run(main())
