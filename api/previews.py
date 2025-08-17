from http.server import BaseHTTPRequestHandler
import json
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import locale
import concurrent.futures

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

def get_article_preview_data(article_url):
    try:
        response = requests.get(article_url, headers=HEADERS, cookies=COOKIES, timeout=8)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'lxml')
        timestamp = None
        for line in soup.select('.article-metaline, .article-metaline-right'):
            if line.select_one('.article-meta-tag') and line.select_one('.article-meta-tag').get_text(strip=True) == '時間':
                timestamp = line.select_one('.article-meta-value').get_text(strip=True)
                break
        
        # === 修改：抓取最多兩張圖片 ===
        image_urls, snippet = [], ""
        main_content = soup.select_one('#main-content')
        if main_content:
            # 使用 dict.fromkeys 來確保 URL 的唯一性，同時保持順序
            unique_links = dict.fromkeys(link.get('href', '') for link in main_content.select('a'))
            for href in unique_links:
                if href and IMAGE_REGEX.search(href):
                    image_urls.append(href)
                    if len(image_urls) == 2:
                        break # 找到兩張就停止

            for tag in main_content.select('.article-metaline, .article-metaline-right, .push, .f2, script, style, a'):
                if tag.name == 'a' and IMAGE_REGEX.search(tag.get('href', '')):
                    continue
                tag.decompose()
            full_text = main_content.get_text(strip=True)
            if not full_text.strip().lower().startswith(('http://', 'https://')):
                 snippet = full_text[:120]
        # === 修改：回傳 thumbnails 陣列 ===
        return {"link": article_url, "thumbnails": image_urls, "formatted_timestamp": format_ptt_time(timestamp), "snippet": snippet, "error": None}
    except Exception as e:
        return {"link": article_url, "thumbnails": [], "formatted_timestamp": "無法載入", "snippet": "無法載入預覽...", "error": str(e)}

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
