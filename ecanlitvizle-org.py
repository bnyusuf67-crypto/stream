import os
import re
import shutil
from html import unescape
from typing import Dict, List, Optional
from urllib.parse import urlparse
import requests

# GitHub Actions ortam değişkenlerinden bilgileri çekme
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

def parse_channel_list(soup) -> List[Dict[str, str]]:
    """HTML içeriğinden kanal öğelerini çıkarır."""
    channels = []
    from bs4 import BeautifulSoup
    
    kanal_liste = soup.find("ul", class_="kanallar")
    if not kanal_liste:
        return channels
        
    for kanal in kanal_liste.find_all("li"):
        a_tag = kanal.find("a")
        img_tag = kanal.find("img")
        if not a_tag or not img_tag:
            continue
            
        link = a_tag.get("href")
        title = a_tag.get("title")
        img = img_tag.get("src")
        
        param = ""
        try:
            r2 = requests.get(link, headers=HEADERS, timeout=10)
            match = re.search(PATTERN_EMBED, r2.text)
            if match:
                param = match.group(1).replace('\\/', '/').split("=")[-1]
        except Exception as e:
            print(f"Kanal detay isteği başarısız: {link} - {e}")

        channels.append({
            "name": title,
            "img": img,
            "param": param
        })
    return channels

def get_ecanlitv() -> List[Dict[str, str]]:
    """Tüm sayfalardaki kanalları tarar."""
    from bs4 import BeautifulSoup
    
    url = "https://www.ecanlitvizle.live/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.content, "html.parser")
    except Exception as e:
        print(f"Ana sayfa çekilemedi: {e}")
        return []

    kanallar = parse_channel_list(soup)

    # Diğer sayfaları tara
    nav = soup.find("div", attrs={"id": "navigation"})
    if nav:
        for page in nav.find_all("a"):
            page_link = page.get("href")
            try:
                r_page = requests.get(page_link, headers=HEADERS, timeout=10)
                soup_page = BeautifulSoup(r_page.content, "html.parser")
                kanallar.extend(parse_channel_list(soup_page))
            except Exception as e:
                print(f"Sayfa çekilemedi {page_link}: {e}")

    return kanallar

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

def save_file(path: str, streams: List[str]) -> bool:
    """Yayın akışlarını M3U8 formatında kaydeder."""
    try:
        if len(streams) == 1:
            url = streams[0]
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code != 200:
                print(f"Yayın adresi çekilemedi: {url}")
                return False

            with open(path, "w", encoding="utf-8") as f:
                if "EXT-X-STREAM-INF" in r.text:
                    base_url = url.rsplit("/", 1)[0]
                    for line in r.text.splitlines():
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
        print(f"Dosya oluşturma hatası ({path}): {e}")
        return False

def get_stream_urls(param: str, yayin: int = 1) -> Optional[List[str]]:
    if yayin > 3 or not param:
        return None

    url = f"https://tv.ecanlitvizle.org/embed.php?kanal={param}&yayin={yayin}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        html_content = r.text
        
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
            return get_stream_urls(param, yayin + 1)
            
    except Exception as e:
        print(f"Yayın akışı ayrıştırma hatası ({param}): {e}")
        return None

if __name__ == "__main__":
    kanallar = get_ecanlitv()
    
    shutil.rmtree(FILE_NAME, ignore_errors=True)
    os.makedirs(FILE_NAME, exist_ok=True)
    os.makedirs("playlists", exist_ok=True)

    playlist_file_path = os.path.join("playlists", f"{FILE_NAME}.m3u")

    with open(playlist_file_path, "w", encoding="utf-8") as playlist_file:
        playlist_file.write("#EXTM3U\n")

        for kanal in kanallar:
            if not kanal["param"]:
                continue

            print(f"İşleniyor: {kanal['name']}")
            stream_urls = get_stream_urls(kanal['param'])

            if stream_urls:
                channel_slug = urlparse(stream_urls[0]).path.split('/')[-1].split('.')[0].replace("-master", "")
                file_name = f"{channel_slug}.m3u8"
                file_path = os.path.join(FILE_NAME, file_name)

                if save_file(file_path, stream_urls):
                    github_url = f"{BASE_URL}/{FILE_NAME}/{file_name}"
                    playlist_file.write(f'#EXTINF:-1 tvg-id="" tvg-name="{kanal["name"]}" tvg-logo="{kanal["img"]}",{kanal["name"]}\n')
                    playlist_file.write(f"#EXTVLCOPT:http-user-agent={HEADERS['User-Agent']}\n")
                    playlist_file.write(f"{github_url}\n")

    print("Oynatma listesi başarıyla oluşturuldu!")
