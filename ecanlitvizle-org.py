import asyncio
import os
import re
import shutil
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

# Sunucuyu kilitlememek için eşzamanlı kanal işleme sınırı
CONCURRENCY_LIMIT = 15
semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

# Kanal başı maksimum yeniden deneme sayısı
MAX_RETRIES = 3

KANAL_PATTERN = re.compile(r'<li>\s*<a\s+href="https://www\.ecanlitvizle\.live/([^"]+)-canli-izle/?(?:\d+)?"\s+title="([^"]+)".*?<img\s+src="([^"]+)"', re.DOTALL)
QUALITY_PATTERN = re.compile(r'["\']#kalite(\d+)["\'].*?changeVideo\(["\']([^"\']+)["\']\)', re.DOTALL)
FILE_PATTERN = re.compile(r"file\s*:\s*['\"]([^'\"]+)['\"]", re.I)


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


async def get_all_channels(session: aiohttp.ClientSession) -> List[Dict[str, str]]:
    main_url = "https://www.ecanlitvizle.live/"
    
    # Ana sayfa gelene kadar tekrar dener
    html = ""
    for _ in range(MAX_RETRIES):
        try:
            async with session.get(main_url, headers=HEADERS) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    break
        except Exception:
            await asyncio.sleep(0.5)

    if not html:
        return []

    page_links = list(set(re.findall(r'href="(https://www\.ecanlitvizle\.live/sayfa/\d+/)"', html)))
    page_links.append(main_url)

    async def fetch_page(p_url):
        for _ in range(MAX_RETRIES):
            try:
                async with session.get(p_url, headers=HEADERS) as r:
                    if r.status == 200:
                        return await r.text()
            except Exception:
                await asyncio.sleep(0.3)
        return ""

    pages_html = await asyncio.gather(*[fetch_page(u) for u in page_links])
    
    channels = {}
    for p_html in pages_html:
        for slug, title, img in KANAL_PATTERN.findall(p_html):
            clean_param = slug.strip('/')
            if clean_param not in channels:
                channels[clean_param] = {
                    "name": title,
                    "img": img,
                    "param": clean_param
                }

    return list(channels.values())


async def fetch_embed_with_retry(session: aiohttp.ClientSession, param: str, yayin: int) -> Optional[List[str]]:
    """Belirli bir yayın alternatifini (yayin=1,2,3) başarılı olana kadar dener."""
    url = f"https://www.ecanlitvizle.live/embed.php?kanal={param}&yayin={yayin}"
    
    for attempt in range(MAX_RETRIES):
        try:
            async with session.get(url, headers=HEADERS) as resp:
                if resp.status == 200:
                    html_content = await resp.text()
                    qualities = dict(QUALITY_PATTERN.findall(html_content))

                    decoded_streams = []
                    for enc_url in qualities.values():
                        enc_url = enc_url.strip()
                        if 'Äx|Xf|x' in enc_url:
                            d_url = decode_video_url(enc_url)
                            if d_url: decoded_streams.append(d_url)
                        else:
                            decoded_streams.append(enc_url)

                    if not decoded_streams:
                        match = FILE_PATTERN.search(html_content)
                        if match:
                            stream = match.group(1).strip()
                            decoded_stream = decode_video_url(stream) if 'Äx|Xf|x' in stream else stream
                            decoded_streams.append(decoded_stream)

                    if decoded_streams and DOMAIN in decoded_streams[0]:
                        return decoded_streams
        except Exception:
            pass
        
        # İstek başarısız olursa kısa bir süre bekleyip tekrar dener
        await asyncio.sleep(0.5)

    return None


async def get_stream_urls(session: aiohttp.ClientSession, param: str) -> Optional[List[str]]:
    """Yayın 1'den başlayarak alternatif yayınları sırayla dener, bulduğu ilk çalışan akışı döndürür."""
    async with semaphore:
        for yayin_no in [1, 2, 3]:
            streams = await fetch_embed_with_retry(session, param, yayin_no)
            if streams:
                return streams
    return None


async def process_channel(session: aiohttp.ClientSession, kanal: Dict[str, str], playlist_lines: List[str]):
    stream_urls = await get_stream_urls(session, kanal['param'])
    if stream_urls:
        channel_slug = urlparse(stream_urls[0]).path.split('/')[-1].split('.')[0].replace("-master", "")
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

        print("İşlem tamamlandı.")


if __name__ == "__main__":
    asyncio.run(main())
