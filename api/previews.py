from http.server import BaseHTTPRequestHandler
import json
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import locale
import concurrent.futures
import time

# --- 設定與常數 ---
try:
    locale.setlocale(locale.LC_TIME, 'zh_TW.UTF-8')
except locale.Error:
    print("警告: 無法設定 'zh_TW.UTF-8' locale。")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}
COOKIES = {'over18': '1'}
IMAGE_REGEX = re.compile(r'\.(jpg|jpeg|png|gif|avif|webp)$', re.IGNORECASE)
YOUTUBE_REGEX = re.compile(r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:watch\?v=|embed\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})')

# --- 核心函式 ---
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

# === 修改：加入重試機制 ===
def get_article_preview_data(article_url):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.get(article_url, headers=HEADERS, cookies=COOKIES, timeout=8)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'lxml')
            timestamp = None
            for line in soup.select('.article-metaline, .article-metaline-right'):
                if line.select_one('.article-meta-tag') and line.select_one('.article-meta-tag').get_text(strip=True) == '時間':
                    timestamp = line.select_one('.article-meta-value').get_text(strip=True)
                    break
            
            thumbnail_url, snippet = None, ""
            main_content = soup.select_one('#main-content')
            if main_content:
                for link in main_content.select('a'):
                    href = link.get('href', '')
                    yt_match = YOUTUBE_REGEX.search(href)
                    if yt_match:
                        video_id = yt_match.group(1)
                        thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                        break
                
                if not thumbnail_url:
                    for link in main_content.select('a'):
                        href = link.get('href', '')
                        if href and IMAGE_REGEX.search(href):
                            thumbnail_url = href
                            break

                for tag in main_content.select('.article-metaline, .article-metaline-right, .push, .f2, script, style, a'):
                    if tag.name == 'a' and (IMAGE_REGEX.search(tag.get('href', '')) or YOUTUBE_REGEX.search(tag.get('href', ''))):
                        continue
                    tag.decompose()
                full_text = main_content.get_text(strip=True)
                if not full_text.strip().lower().startswith(('http://', 'https://')):
                     snippet = full_text[:120]
            
            # 成功抓取，直接回傳
            return {"link": article_url, "thumbnail": thumbnail_url, "formatted_timestamp": format_ptt_time(timestamp), "snippet": snippet, "error": None}
        
        except Exception as e:
            # 如果是最後一次嘗試，則回傳錯誤
            if attempt >= max_retries - 1:
                return {"link": article_url, "thumbnail": None, "formatted_timestamp": "無法載入", "snippet": "無法載入預覽...", "error": str(e)}
            # 否則等待後重試
            time.sleep(1)

# --- Vercel 的 Serverless Function 入口 ---
class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        data = {}
        error = None
        ordered_results = []
        
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            body = json.loads(post_data)

            if 'urls' not in body or not isinstance(body['urls'], list):
                raise ValueError("無效的請求格式")

            urls = body['urls']
            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                future_to_url = {executor.submit(get_article_preview_data, url): url for url in urls}
                for future in concurrent.futures.as_completed(future_to_url):
                    results.append(future.result())
            ordered_results = sorted(results, key=lambda r: urls.index(r['link']))
            data = ordered_results

        except Exception as e:
            error = e
            print(f"Error in /api/previews: {e}")
            data = {"error": str(e)}

        self.send_response(500 if error else 200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))
        return
