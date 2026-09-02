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

# Ağ istek sınırı
CONCURRENCY_LIMIT = 50
semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

# Regex Desenleri (HTML parse yerine mikro saniyelik Regex)
KANAL_PATTERN = re.compile(r'<li>\s*<a\s+href="([^"]+)"\s+title="([^"]+)".*?<img\s+src="([^"]+)"', re.DOTALL)
EMBED_PATTERN = re.compile(r'"embedUrl":\s*"(.*?)"')
QUALITY_PATTERN = re.compile(r'["\']#kalite(\d+)["\'].*?changeVideo\(["\']([^"\']+)["\']\)', re.DOTALL)
FILE_PATTERNS = [
    re.compile(r"file\s*:\s*['\"]([^'\"]+)['\"]", re.I),
    re.compile(r"file\s*=\s*['\"]([^'\"]+)['\"]", re.I),
    re.compile(r"file\s*:\s*&#039;([^&#039;]+)&#039;", re.I)
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


async def fetch_channel_info(session: aiohttp.ClientSession, url: str, name: str, img: str) -> Optional[Dict[str, str]]:
    """Kanal detayını HTML parse etmeden doğrudan Regex ile çeker."""
    async with semaphore:
        try:
            async with session.get(url, headers=HEADERS, timeout=3) as resp:
                text = await resp.text()
                match = EMBED_PATTERN.search(text)
                if match:
                    param = match.group(1).replace('\\/', '/').split("=")[-1]
                    return {"name": name, "img": img, "param": param}
        except Exception:
            pass
    return None


async def get_all_channels(session: aiohttp.ClientSession) -> List[Dict[str, str]]:
    """Ana sayfayı ve nav sayfalarını paralel indirip Regex ile kanalları ayıklar."""
    main_url = "https://www.ecanlitvizle.live/"
    try:
        async with session.get(main_url, headers=HEADERS, timeout=4) as resp:
            html = await resp.text()
    except Exception:
        return []

    # Sayfalama linklerini Regex ile yakala
    page_links = list(set(re.findall(r'href="(https://www\.ecanlitvizle\.live/sayfa/\d+/)"', html)))
    page_links.append(main_url)

    # Bütün sayfaları aynı anda indir
    async def fetch_page(p_url):
        try:
            async with session.get(p_url, headers=HEADERS, timeout=3) as r:
                return await r.text()
        except Exception:
            return ""

    pages_html = await asyncio.gather(*[fetch_page(u) for u in page_links])
    
    # Tüm HTML'lerden kanal verilerini topla
    raw_channels = []
    for p_html in pages_html:
        for link, title, img in KANAL_PATTERN.findall(p_html):
            raw_channels.append((link, title, img))

    # Tekrarlayan kanalları temizle
    unique_channels = {c[0]: c for c in raw_channels}.values()

    # Kanal parametrelerini paralel çek
    results = await asyncio.gather(*[fetch_channel_info(session, link, title, img) for link, title, img in unique_channels])
    return [r for r in results if r is not None]


async def get_stream_urls(session: aiohttp.ClientSession, param: str, yayin: int = 1) -> Optional[List[str]]:
    if yayin > 3 or not param:
        return None

    url = f"https://www.ecanlitvizle.live/embed.php?kanal={param}&yayin={yayin}"
    async with semaphore:
        try:
            async with session.get(url, headers=HEADERS, timeout=3) as resp:
                if resp.status != 200:
                    return await get_stream_urls(session, param, yayin + 1)

                html_content = unescape(await resp.text())
                
                # Kalite opsiyonlarını çöz
                qualities = {}
                for res, enc_url in QUALITY_PATTERN.findall(html_content):
                    qualities[res] = enc_url.strip()

                decoded_streams = []
                for enc_url in qualities.values():
                    if 'Äx|Xf|x' in enc_url:
                        d_url = decode_video_url(enc_url)
                        if d_url: decoded_streams.append(d_url)
                    else:
                        decoded_streams.append(enc_url)

                if not decoded_streams:
                    for pat in FILE_PATTERNS:
                        m = pat.search(html_content)
                        if m:
                            stream = m.group(1).strip()
                            decoded_stream = decode_video_url(stream) if 'Äx|Xf|x' in stream else stream
                            decoded_streams.append(decoded_stream)
                            break

                if decoded_streams and DOMAIN in decoded_streams[0]:
                    return decoded_streams
                else:
                    return await get_stream_urls(session, param, yayin + 1)
        except Exception:
            return None


def fast_save_file(path: str, streams: List[str]):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n#EXT-X-VERSION:3\n")
            if len(streams) == 1:
                f.write("#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=800000\n")
                f.write(f"{streams[0]}\n")
            else:
                bw_list = ["800000", "1200000", "1800000", "2500000", "3000000"]
                for i, url in enumerate(streams):
                    bw = bw_list[i] if i < len(bw_list) else "3000000"
                    f.write(f"#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH={bw}\n{url}\n")
        return True
    except Exception:
        return False


async def process_channel(session: aiohttp.ClientSession, kanal: Dict[str, str], playlist_lines: List[str]):
    stream_urls = await get_stream_urls(session, kanal['param'])
    if stream_urls:
        channel_slug = urlparse(stream_urls[0]).path.split('/')[-1].split('.')[0].replace("-master", "")
        file_name = f"{channel_slug}.m3u8"
        file_path = os.path.join(FILE_NAME, file_name)

        if fast_save_file(file_path, stream_urls):
            github_url = f"{BASE_URL}/{FILE_NAME}/{file_name}"
            playlist_lines.append(f'#EXTINF:-1 tvg-id="" tvg-name="{kanal["name"]}" tvg-logo="{kanal["img"]}",{kanal["name"]}\n')
            playlist_lines.append(f"#EXTVLCOPT:http-user-agent={HEADERS['User-Agent']}\n")
            playlist_lines.append(f"{github_url}\n")


async def main():
    connector = aiohttp.TCPConnector(limit=300, limit_per_host=100, ttl_dns_cache=300)
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

        print("Süreç tamamlandı.")


if __name__ == "__main__":
    asyncio.run(main())
