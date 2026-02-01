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
# 只解析這些標籤，加速 3-5 倍
meta_strainer = SoupStrainer(class_=['article-metaline', 'article-metaline-right', 'push', 'f2', 'article-meta-tag', 'article-meta-value'])
main_strainer = SoupStrainer(id='main-content')

def format_ptt_time(time_str):
    if not time_str: return None
    try:
        dt_obj = datetime.strptime(time_str, '%a %b %d %H:%M:%S %Y')
        weekday_map = {'Monday': '週一', 'Tuesday': '週二', 'Wednesday': '週三', 'Thursday': '週四', 'Friday': '週五', 'Saturday': '週六', 'Sunday': '週日'}
        weekday_en = dt_obj.strftime('%A')
        weekday_zh = weekday_map.get(weekday_en, '')
        return dt_obj.strftime(f'%Y %m %d %H:%M {weekday_zh}')
    except (ValueError, TypeError):
        return time_str

def get_article_preview_data(article_url):
    try:
        response = session.get(article_url, timeout=6)
        if response.status_code != 200:
             return {"link": article_url, "error": f"Status {response.status_code}"}

        soup = BeautifulSoup(response.text, 'lxml', parse_only=main_strainer)
        main_content = soup.select_one('#main-content')
        
        if not main_content:
             return {"link": article_url, "error": "No content"}

        timestamp = None
        for line in main_content.select('.article-metaline, .article-metaline-right'):
            tag = line.select_one('.article-meta-tag')
            if tag and tag.get_text(strip=True) == '時間':
                timestamp = line.select_one('.article-meta-value').get_text(strip=True)
                break
        
        thumbnail_url, snippet = None, ""
        
        # 尋找圖片/影片
        all_links = main_content.find_all('a', href=True)
        for link in all_links:
            href = link['href']
            yt_match = YOUTUBE_REGEX.search(href)
            if yt_match:
                thumbnail_url = f"https://i.ytimg.com/vi/{yt_match.group(1)}/hqdefault.jpg"
                break
        
        if not thumbnail_url:
            for link in all_links:
                href = link['href']
                if IMAGE_REGEX.search(href):
                    thumbnail_url = href
                    break

        # 清理雜訊
        for tag in main_content.select('.article-metaline, .article-metaline-right, .push, .f2, script, style'):
            tag.decompose()
        for a in main_content.find_all('a'):
            a.decompose()

        full_text = main_content.get_text(strip=True)
        snippet = full_text[:100]

        return {
            "link": article_url, 
            "thumbnail": thumbnail_url, 
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
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            body = json.loads(post_data)

            if 'urls' not in body or not isinstance(body['urls'], list):
                raise ValueError("無效的請求格式")

            urls = body['urls']
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(get_article_preview_data, urls))
            data = results

        except Exception as e:
            error = e
            data = {"error": str(e)}

        self.send_response(500 if error else 200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Cache-Control', 'public, max-age=60')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))
        return