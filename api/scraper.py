from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import re
from datetime import datetime, date, timedelta
import locale
import concurrent.futures
import random

# --- 設定與常數 ---
try:
    locale.setlocale(locale.LC_TIME, 'zh_TW.UTF-8')
except locale.Error:
    pass

def create_session():
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retries)
    s.mount('https://', adapter)
    s.mount('http://', adapter)
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.ptt.cc/bbs/Beauty/index.html'
    })
    s.cookies.update({'over18': '1'})
    return s

session = create_session()

IMAGE_REGEX = re.compile(r'\.(jpg|jpeg|png|gif|avif|webp)$', re.IGNORECASE)
# 強化版 YouTube Regex
YOUTUBE_REGEX = re.compile(r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:watch\?v=|embed\/|shorts\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})')

# 已從列表移除: NBA, Baseball, Car, C_Chat
HOT_SCRAPE_BOARDS = [ "Gossiping", "Beauty", "Stock", "Lifeismoney", "MobileComm", "Boy-Girl", "Tech_Job", "HatePolitics", "KoreaStar", "movie", "e-shopping", "Sex" ]

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
            
        title_text = title_tag.text.strip()
        if title_text.startswith('[公告]') or '公告' in title_text[:4]:
            return None

        push_count_text = push_tag.get_text(strip=True) if push_tag else ''
        push_count = 0
        if push_count_text:
            if push_count_text == '爆': push_count = '爆'
            elif push_count_text.startswith('X'): push_count = push_count_text
            else:
                try: push_count = int(push_count_text)
                except (ValueError, TypeError): push_count = 0
                
        return {
            "title": title_text, 
            "link": "https://www.ptt.cc" + title_tag['href'], 
            "board": board, 
            "author": meta_tag.select_one('.author').get_text(strip=True) or '', 
            "date": meta_tag.select_one('.date').get_text(strip=True) or '', 
            "push_count": push_count
        }
    except Exception:
        return None

def fetch_ptt_article_list(board, start_url, min_items=15, max_pages=3):
    all_articles = []
    current_url = start_url
    final_prev_url = None
    
    for _ in range(max_pages):
        try:
            response = session.get(current_url, timeout=8)
            if response.status_code == 404: break
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'lxml')
            prev_page_link_tag = soup.select_one('a.btn.wide:-soup-contains("上頁")')
            
            articles_tags = soup.select('div.r-ent')
            page_articles = [data for item in articles_tags if (data := process_article_item_basic(item, board)) is not None]
            
            page_articles.reverse()
            all_articles.extend(page_articles)
            
            if prev_page_link_tag:
                final_prev_url = "https://www.ptt.cc" + prev_page_link_tag['href']
                current_url = final_prev_url
            else:
                final_prev_url = None
                break
                
            if len(all_articles) >= min_items:
                break
                
        except Exception as e:
            print(f"Fetch list error: {e}")
            break
            
    return {"articles": all_articles, "prev_page_url": final_prev_url}

def fetch_ptt_article_content(article_url):
    try:
        response = session.get(article_url, timeout=10)
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
        
        pushes = []
        for push in main_content.select('.push'):
            push_tag_span = push.select_one('.push-tag')
            push_userid_span = push.select_one('.push-userid')
            push_content_span = push.select_one('.push-content')
            push_ipdatetime_span = push.select_one('.push-ipdatetime')
            
            pushes.append({ 
                "tag": push_tag_span.get_text(strip=True) if push_tag_span else '', 
                "user": push_userid_span.get_text(strip=True) if push_userid_span else '', 
                "content": push_content_span.get_text(strip=True) if push_content_span else '', 
                "time": push_ipdatetime_span.get_text(strip=True) if push_ipdatetime_span else '' 
            })
            push.decompose()

        images, videos = [], []
        for link in main_content.select('a'):
            href = link.get('href', '')
            if IMAGE_REGEX.search(href): images.append(href)
            else:
                yt_match = YOUTUBE_REGEX.search(href)
                if yt_match: videos.append({"id": yt_match.group(1), "url": href})

        for tag in main_content.select('span.f2, script, style'): tag.decompose()
        for br in main_content.find_all("br"): br.replace_with("\n")
        
        full_text = main_content.get_text()
        content_parts = re.split(r'--\n※ 發信站: 批踢踢實業坊\(ptt\.cc\), 來自:', full_text)
        content = content_parts[0].strip()
        
        return { "author_full": author_full, "formatted_timestamp": format_ptt_time(timestamp), "content": content, "images": list(dict.fromkeys(images)), "videos": list({v['id']: v for v in videos}.values()), "pushes": pushes }
    except Exception as e:
        print(f"Content fetch error: {e}")
        raise e

def parse_push_count_for_sort(c):
    if c == '爆': return 1000
    if isinstance(c, str):
        if c.startswith('X'):
            try: return -10 - int(c[1:])
            except: return -100
        try: return int(c)
        except: return 0
    return c if isinstance(c, int) else 0

def fetch_one_board_page(board):
    try:
        url = f"https://www.ptt.cc/bbs/{board}/index.html"
        data = fetch_ptt_article_list(board, url, min_items=1, max_pages=1)
        return data.get("articles", [])
    except Exception: return []

def fetch_ptt_hot_articles():
    all_articles = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_board = {executor.submit(fetch_one_board_page, board): board for board in HOT_SCRAPE_BOARDS}
        for future in concurrent.futures.as_completed(future_to_board):
            try: all_articles.extend(future.result())
            except Exception: pass
            
    today = date.today()
    yesterday = today - timedelta(days=1)
    
    # 建立多種日期格式以匹配 PTT 的各種寫法
    valid_dates = set()
    for d in [today, yesterday]:
        valid_dates.add(d.strftime('%m/%d'))      # 格式: 02/03
        try: valid_dates.add(d.strftime('%-m/%-d')) # 格式: 2/3 (Linux)
        except: pass
        try: valid_dates.add(d.strftime('%#m/%#d')) # 格式: 2/3 (Windows)
        except: pass
        # PTT 常見格式: 2/03 (月份不補零，日期補零)
        valid_dates.add(f"{d.month}/{d.day:02d}")
        # PTT 有時也會出現: 2/3 (都不補零)
        valid_dates.add(f"{d.month}/{d.day}")
    
    # 只要文章日期包含在有效日期集合中，就保留
    recent_articles = [a for a in all_articles if any(d in a.get('date', '') for d in valid_dates)]
    
    sorted_articles = sorted(recent_articles, key=lambda x: parse_push_count_for_sort(x.get('push_count', 0)), reverse=True)
    return {"articles": sorted_articles[:100], "prev_page_url": None}

def fetch_beauty_gallery_data(list_url=None):
    current_list_url = list_url if list_url else "https://www.ptt.cc/bbs/Beauty/index.html"
    list_data = fetch_ptt_article_list("Beauty", current_list_url, min_items=15, max_pages=2)
    
    page_articles = list_data.get("articles", [])
    final_prev_page_url = list_data.get("prev_page_url")

    gallery_items = []
    def process_article_for_gallery(article):
        try:
            content_data = fetch_ptt_article_content(article['link'])
            images = content_data.get('images', [])
            if not images: return None
            return { "article": article, "all_images": images, "preview_image": random.choice(images) }
        except Exception: return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_article = {executor.submit(process_article_for_gallery, article): article for article in page_articles}
        for future in concurrent.futures.as_completed(future_to_article):
            result = future.result()
            if result: gallery_items.append(result)
            
    return {"articles": gallery_items, "prev_page_url": final_prev_page_url}

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse(self.path)
        query_params = parse_qs(unquote(parsed_path.query))
        data, error = {}, None
        
        try:
            if 'proxy_url' in query_params:
                image_url = query_params['proxy_url'][0].replace('http://', 'https://', 1)
                proxy_headers = {'User-Agent': session.headers['User-Agent'], 'Referer': image_url}
                resp = requests.get(image_url, headers=proxy_headers, timeout=20, stream=True)
                resp.raise_for_status()
                
                self.send_response(200)
                if 'Content-Type' in resp.headers: self.send_header('Content-Type', resp.headers['Content-Type'])
                self.send_header('Cache-Control', 'public, max-age=604800, immutable')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                for chunk in resp.iter_content(chunk_size=32768): self.wfile.write(chunk)
                return

            board = query_params.get('board', [None])[0]
            if board == 'Hot': 
                data = fetch_ptt_hot_articles()
            elif board == 'BeautyGallery':
                list_url = query_params.get('list_url', [None])[0]
                data = fetch_beauty_gallery_data(list_url)
            elif 'list_url' in query_params and board: 
                data = fetch_ptt_article_list(board, query_params['list_url'][0], min_items=15)
            elif 'article_url' in query_params: 
                data = fetch_ptt_article_content(query_params['article_url'][0])
            elif board: 
                data = fetch_ptt_article_list(board, f"https://www.ptt.cc/bbs/{board}/index.html", min_items=20)
            else: 
                raise ValueError("無效參數")

        except Exception as e:
            error = e
            print(f"Scraper Error: {e}")
            data = {"error": str(e)}

        self.send_response(500 if error else 200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))
        return