from http.server import BaseHTTPRequestHandler
import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup, SoupStrainer
import re
from datetime import datetime
import locale
import concurrent.futures

try:
    locale.setlocale(locale.LC_TIME, 'zh_TW.UTF-8')
except locale.Error:
    pass

def create_session():
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retries)
    s.mount('https://', adapter)
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    })
    s.cookies.update({'over18': '1'})
    return s

session = create_session()

IMAGE_REGEX = re.compile(r'\.(jpg|jpeg|png|gif|avif|webp)$', re.IGNORECASE)
YOUTUBE_REGEX = re.compile(r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:watch\?v=|embed\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})')

# 優化：只解析需要的區塊
main_strainer = SoupStrainer('div', id='main-content')

def format_ptt_time(time_str):
    if not time_str: return None
    try:
        dt_obj = datetime.strptime(time_str, '%a %b %d %H:%M:%S %Y')
        return dt_obj.strftime('%Y/%m/%d %H:%M')
    except:
        return time_str

def get_article_preview_data(article_url):
    try:
        response = session.get(article_url, timeout=6)
        if response.status_code != 200:
            return {"link": article_url, "error": "Fetch failed"}

        # 使用 strainer 加速解析
        soup = BeautifulSoup(response.text, 'lxml', parse_only=main_strainer)
        main_content = soup.select_one('#main-content')
        
        if not main_content:
            return {"link": article_url, "error": "No content"}

        # 提取時間
        timestamp = ''
        metas = main_content.select('.article-metaline .article-meta-value')
        if len(metas) >= 3:
            timestamp = metas[2].text.strip()

        # 提取圖片 (最多 3 張，用於列表拼貼)
        images = []
        seen_images = set()
        
        # 1. 先找 YouTube 縮圖
        for link in main_content.find_all('a', href=True):
            href = link['href']
            yt_match = YOUTUBE_REGEX.search(href)
            if yt_match:
                yt_thumb = f"https://i.ytimg.com/vi/{yt_match.group(1)}/hqdefault.jpg"
                if yt_thumb not in seen_images:
                    images.append(yt_thumb)
                    seen_images.add(yt_thumb)
            
            if IMAGE_REGEX.search(href):
                if href not in seen_images:
                    images.append(href)
                    seen_images.add(href)
            
            if len(images) >= 3: # 限制抓取 3 張
                break

        # 2. 如果圖片不夠，嘗試找 img 標籤 (PTT 網頁版有時會自動展開圖片)
        if len(images) < 3:
             for img in main_content.find_all('img', src=True):
                src = img['src']
                if src not in seen_images:
                     images.append(src)
                     seen_images.add(src)
                if len(images) >= 3:
                    break
        
        # 清理 DOM 以提取純文字摘要
        for tag in main_content.select('.article-metaline, .article-metaline-right, .push, .f2, script, style'):
            tag.decompose()
        for a in main_content.find_all('a'):
            a.decompose()

        full_text = main_content.get_text(strip=True)
        snippet = full_text[:100]

        return {
            "link": article_url, 
            "images": images, # 回傳圖片列表
            "thumbnail": images[0] if images else None, # 相容舊欄位
            "formatted_timestamp": format_ptt_time(timestamp), 
            "snippet": snippet, 
            "error": None
        }
    except Exception as e:
        return {"link": article_url, "error": str(e)}

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        data = {}
        error = None
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            body = json.loads(post_data)

            if 'urls' not in body or not isinstance(body['urls'], list):
                raise ValueError("無效的請求格式")

            urls = body['urls']
            # 使用多執行緒並行抓取，加快列表載入速度
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(get_article_preview_data, urls))
            data = results

        except Exception as e:
            error = e
            data = {"error": str(e)}

        self.send_response(500 if error else 200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Cache-Control', 'public, max-age=300')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))