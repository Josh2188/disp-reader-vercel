from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import locale

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

def process_article_item_basic(item, board):
    try:
        title_tag = item.select_one('.title a')
        meta_tag = item.select_one('.meta')
        push_tag = item.select_one('.nrec span')
        if not (title_tag and title_tag.get('href') and meta_tag) or "本文已被刪除" in title_tag.text:
            return None
        push_count_text = push_tag.get_text(strip=True) if push_tag else ''
        push_count = 0
        if push_count_text:
            if push_count_text == '爆': push_count = '爆'
            elif push_count_text.startswith('X'): push_count = push_count_text
            else:
                try: push_count = int(push_count_text)
                except (ValueError, TypeError): push_count = 0
        return {"title": title_tag.text.strip(), "link": "https://www.ptt.cc" + title_tag['href'], "board": board, "author": meta_tag.select_one('.author').get_text(strip=True) or '', "date": meta_tag.select_one('.date').get_text(strip=True) or '', "push_count": push_count}
    except Exception:
        return None

def fetch_ptt_article_list(board, page_url):
    try:
        response = requests.get(page_url, headers=HEADERS, cookies=COOKIES, timeout=10)
        response.raise_for_status()
    except requests.exceptions.HTTPError as err:
        if err.response.status_code == 404:
            return {"articles": [], "prev_page_url": None}
        raise
    soup = BeautifulSoup(response.text, 'lxml')
    articles_tags = soup.select('div.r-ent')
    article_list = [data for item in articles_tags if (data := process_article_item_basic(item, board)) is not None]
    article_list.reverse()
    prev_page_link_tag = soup.select_one('a.btn.wide:-soup-contains("上頁")')
    prev_page_url = "https://www.ptt.cc" + prev_page_link_tag['href'] if prev_page_link_tag else None
    return {"articles": article_list, "prev_page_url": prev_page_url}

def fetch_ptt_article_content(article_url):
    response = requests.get(article_url, headers=HEADERS, cookies=COOKIES, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'lxml')
    main_content = soup.select_one('#main-content')
    if not main_content: raise Exception("找不到主要內容區塊。")
    author_full, timestamp = '', ''
    for line in main_content.select('.article-metaline, .article-metaline-right'):
        if line.select_one('.article-meta-tag'):
            tag = line.select_one('.article-meta-tag').get_text(strip=True)
            value = line.select_one('.article-meta-value').get_text(strip=True)
            if tag == '作者': author_full = value
            elif tag == '時間': timestamp = value
        line.decompose()
    for p in main_content.select('.push, span.f2, script, style'): p.decompose()
    images = [link.get('href') for link in main_content.select('a') if link.get('href') and IMAGE_REGEX.search(link.get('href'))]
    for br in main_content.find_all("br"): br.replace_with("\n")
    content = main_content.get_text().strip()
    return {"author_full": author_full, "formatted_timestamp": format_ptt_time(timestamp), "content": content, "images": list(dict.fromkeys(images))}

# --- Vercel 的 Serverless Function 入口 ---
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse(self.path)
        query_params = parse_qs(parsed_path.query)
        data = {}
        error = None

        try:
            if 'proxy_url' in query_params:
                # 代理圖片請求，讓瀏覽器自行決定快取策略
                image_url = query_params['proxy_url'][0]
                response = requests.get(image_url, timeout=20, stream=True)
                response.raise_for_status()
                
                self.send_response(200)
                # 轉發原始圖片的 Content-Type
                if 'Content-Type' in response.headers:
                    self.send_header('Content-Type', response.headers['Content-Type'])
                # 讓瀏覽器快取圖片一天
                self.send_header('Cache-Control', 'public, max-age=86400')
                self.end_headers()
                
                # 流式傳輸圖片內容
                for chunk in response.iter_content(chunk_size=8192):
                    self.wfile.write(chunk)
                return

            if 'list_url' in query_params:
                board = query_params.get('board', ['Beauty'])[0]
                list_url = query_params['list_url'][0]
                data = fetch_ptt_article_list(board, list_url)
            elif 'article_url' in query_params:
                article_url = query_params['article_url'][0]
                data = fetch_ptt_article_content(article_url)
            else:
                board = query_params.get('board', ['Beauty'])[0]
                initial_url = f"https://www.ptt.cc/bbs/{board}/index.html"
                data = fetch_ptt_article_list(board, initial_url)

        except Exception as e:
            error = e
            print(f"Error in /api/scraper: {e}")
            data = {"error": str(e)}

        self.send_response(500 if error else 200)
        self.send_header('Content-type', 'application/json')
        # === 加入禁止快取標頭 ===
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))
        return
