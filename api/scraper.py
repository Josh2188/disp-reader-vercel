from flask import Flask, jsonify, request, Response, stream_with_context
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import locale
import concurrent.futures

app = Flask(__name__)

# --- 設定與常數 ---

try:
    locale.setlocale(locale.LC_TIME, 'zh_TW.UTF-8')
except locale.Error:
    print("警告: 無法設定 'zh_TW.UTF-8' locale，星期可能顯示為英文。")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}
COOKIES = {'over18': '1'}
IMAGE_REGEX = re.compile(r'\.(jpg|jpeg|png|gif|avif|webp)$', re.IGNORECASE)

# --- 核心函式 ---

def format_ptt_time(time_str):
    if not time_str:
        return None
    try:
        dt_obj = datetime.strptime(time_str, '%a %b %d %H:%M:%S %Y')
        weekday_map = {
            'Monday': '週一', 'Tuesday': '週二', 'Wednesday': '週三',
            'Thursday': '週四', 'Friday': '週五', 'Saturday': '週六', 'Sunday': '週日'
        }
        weekday_en = dt_obj.strftime('%A')
        weekday_zh = weekday_map.get(weekday_en, '')
        return dt_obj.strftime(f'%Y %m %d %H:%M {weekday_zh}')
    except (ValueError, TypeError):
        return time_str

def get_article_preview_data(article_url):
    """(可平行處理的任務) 獲取單篇文章的預覽資料。"""
    try:
        response = requests.get(article_url, headers=HEADERS, cookies=COOKIES, timeout=8)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        timestamp = None
        for line in soup.select('.article-metaline, .article-metaline-right'):
            if line.select_one('.article-meta-tag') and line.select_one('.article-meta-tag').get_text(strip=True) == '時間':
                timestamp = line.select_one('.article-meta-value').get_text(strip=True)
                break
        
        first_image_url, snippet = None, ""
        main_content = soup.select_one('#main-content')
        if main_content:
            for link in main_content.select('a'):
                href = link.get('href', '')
                if href and IMAGE_REGEX.search(href):
                    first_image_url = href
                    break
            
            for tag in main_content.select('.article-metaline, .article-metaline-right, .push, .f2, script, style, a'):
                if tag.name == 'a' and IMAGE_REGEX.search(tag.get('href', '')):
                    continue
                tag.decompose()
            
            full_text = main_content.get_text(strip=True)
            if not full_text.strip().lower().startswith(('http://', 'https://')):
                 snippet = full_text[:120]

        return {
            "link": article_url, # 回傳 link 以便前端匹配
            "thumbnail": first_image_url,
            "formatted_timestamp": format_ptt_time(timestamp),
            "snippet": snippet,
            "error": None
        }
    except Exception as e:
        print(f"錯誤: 獲取預覽失敗 {article_url}: {e}")
        return {
            "link": article_url,
            "thumbnail": None,
            "formatted_timestamp": "無法載入",
            "snippet": "無法載入預覽...",
            "error": str(e)
        }

def process_article_item_basic(item, board):
    """(快速任務) 處理單個文章列表項目，僅提取最基本的資訊。"""
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
        
        return {
            "title": title_tag.text.strip(),
            "link": "https://www.ptt.cc" + title_tag['href'],
            "board": board,
            "author": meta_tag.select_one('.author').get_text(strip=True) or '',
            "date": meta_tag.select_one('.date').get_text(strip=True) or '',
            "push_count": push_count,
        }
    except Exception:
        return None

def fetch_ptt_article_list(board, page_url):
    """(快速任務) 抓取 PTT 文章列表頁面。"""
    try:
        response = requests.get(page_url, headers=HEADERS, cookies=COOKIES, timeout=10)
        response.raise_for_status()
    except requests.exceptions.HTTPError as err:
        if err.response.status_code == 404:
            return {"articles": [], "prev_page_url": None}
        raise

    soup = BeautifulSoup(response.text, 'html.parser')
    articles_tags = soup.select('div.r-ent')
    article_list = [data for item in articles_tags if (data := process_article_item_basic(item, board)) is not None]
    article_list.reverse()
    
    prev_page_link_tag = soup.select_one('a.btn.wide:-soup-contains("上頁")')
    prev_page_url = "https://www.ptt.cc" + prev_page_link_tag['href'] if prev_page_link_tag else None
    
    return {"articles": article_list, "prev_page_url": prev_page_url}

def fetch_ptt_article_content(article_url):
    """抓取 PTT 文章內文頁面。"""
    response = requests.get(article_url, headers=HEADERS, cookies=COOKIES, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    
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
    
    return {
        "author_full": author_full, "formatted_timestamp": format_ptt_time(timestamp),
        "content": content, "images": list(dict.fromkeys(images)),
    }

def proxy_image_download(proxy_url):
    """代理下載圖片。"""
    try:
        req = requests.get(proxy_url, stream=True, headers=HEADERS, timeout=20)
        req.raise_for_status()
        filename = proxy_url.split('/')[-1].split('?')[0] or 'download'
        return Response(stream_with_context(req.iter_content(chunk_size=8192)),
                        content_type=req.headers.get('content-type'),
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})
    except requests.exceptions.RequestException as e:
        return str(e), 502

# --- API 路由 ---

@app.route('/api/scraper', methods=['GET'])
def scraper_endpoint():
    """主要 API 端點，處理列表、內文和圖片代理。"""
    try:
        if 'proxy_url' in request.args:
            return proxy_image_download(request.args.get('proxy_url'))

        board = request.args.get('board', 'Beauty')
        
        if 'list_url' in request.args:
            return jsonify(fetch_ptt_article_list(board, request.args.get('list_url')))
        
        if 'article_url' in request.args:
            return jsonify(fetch_ptt_article_content(request.args.get('article_url')))
        
        initial_url = f"https://www.ptt.cc/bbs/{board}/index.html"
        return jsonify(fetch_ptt_article_list(board, initial_url))
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# *** 新增的批次處理路由 ***
@app.route('/api/previews', methods=['POST'])
def batch_previews_endpoint():
    """接收一個包含多個 URL 的列表，並行抓取它們的預覽資料。"""
    try:
        data = request.get_json()
        if not data or 'urls' not in data or not isinstance(data['urls'], list):
            return jsonify({"error": "無效的請求格式"}), 400

        urls = data['urls']
        results = []
        # 使用 ThreadPoolExecutor 來並行執行網路請求
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            # map 會保持原始 urls 列表的順序
            future_to_url = {executor.submit(get_article_preview_data, url): url for url in urls}
            for future in concurrent.futures.as_completed(future_to_url):
                results.append(future.result())

        # 為了確保順序，我們將結果重新排序
        ordered_results = sorted(results, key=lambda r: urls.index(r['link']))

        return jsonify(ordered_results)
    except Exception as e:
        print(f"批次處理時發生嚴重錯誤: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
