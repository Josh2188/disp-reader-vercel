from flask import Flask, jsonify, request, Response, stream_with_context
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import locale

app = Flask(__name__)

# --- 設定與常數 ---

# 嘗試設定台灣時區以正確顯示中文星期
try:
    locale.setlocale(locale.LC_TIME, 'zh_TW.UTF-8')
except locale.Error:
    print("警告: 無法設定 'zh_TW.UTF-8' locale，星期可能顯示為英文。")

# 定義請求標頭和 cookies，模擬已登入使用者
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}
COOKIES = {'over18': '1'}

# 用於匹配圖片連結的正規表示式
IMAGE_REGEX = re.compile(r'\.(jpg|jpeg|png|gif|avif|webp)$', re.IGNORECASE)

# --- 核心函式 ---

def format_ptt_time(time_str):
    """
    將 PTT 的英文時間戳轉換為 'YYYY MM DD HH:mm 週X' 格式。
    """
    if not time_str:
        return None
    try:
        # PTT 原始時間格式範例: 'Wed Aug 16 21:13:44 2023'
        dt_obj = datetime.strptime(time_str, '%a %b %d %H:%M:%S %Y')
        
        # 建立英文星期與中文星期的對應字典
        weekday_map = {
            'Monday': '週一', 'Tuesday': '週二', 'Wednesday': '週三',
            'Thursday': '週四', 'Friday': '週五', 'Saturday': '週六', 'Sunday': '週日'
        }
        weekday_en = dt_obj.strftime('%A')
        weekday_zh = weekday_map.get(weekday_en, '')
        
        # 組合出最終的格式
        return dt_obj.strftime(f'%Y %m %d %H:%M {weekday_zh}')
    except (ValueError, TypeError):
        # 如果格式解析失敗，回傳原始字串
        return time_str

def get_article_preview_data(article_url):
    """
    (非同步任務) 獲取單篇文章的預覽資料，包括縮圖、精確時間和內文摘要。
    增加了超時和錯誤處理，確保此函式不會輕易失敗。
    """
    try:
        response = requests.get(article_url, headers=HEADERS, cookies=COOKIES, timeout=8)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 提取精確時間
        timestamp = None
        meta_lines = soup.select('.article-metaline, .article-metaline-right')
        for line in meta_lines:
            tag = line.select_one('.article-meta-tag')
            value = line.select_one('.article-meta-value')
            if tag and value and tag.get_text(strip=True) == '時間':
                timestamp = value.get_text(strip=True)
                break
        
        first_image_url = None
        snippet = ""
        main_content = soup.select_one('#main-content')
        if main_content:
            # 尋找第一個圖片連結作為縮圖
            for link in main_content.select('a'):
                href = link.get('href', '')
                if href and IMAGE_REGEX.search(href):
                    first_image_url = href
                    break
            
            # 移除所有不需要的元素以產生乾淨的內文摘要
            for tag in main_content.select('.article-metaline, .article-metaline-right, .push, .f2, script, style, a'):
                if tag.name == 'a' and IMAGE_REGEX.search(tag.get('href', '')):
                    continue # 保留圖片連結文字本身用於判斷
                tag.decompose()
            
            # 取得純文字並處理
            full_text = main_content.get_text(strip=True)
            # **修正：如果內文開頭是圖片網址，則不顯示預覽**
            if full_text.strip().lower().startswith(('http://', 'https://')):
                 snippet = ""
            else:
                 snippet = full_text[:120] # 稍微增加預覽長度

        return {
            "thumbnail": first_image_url,
            "formatted_timestamp": format_ptt_time(timestamp),
            "snippet": snippet
        }
    except Exception as e:
        print(f"錯誤: 獲取預覽失敗 {article_url}: {e}")
        # 若出錯，回傳一個包含錯誤訊息的 JSON，讓前端知道
        return {"error": str(e), "formatted_timestamp": "無法載入", "snippet": "無法載入預覽..."}, 500

def process_article_item_basic(item, board):
    """
    (快速任務) 處理單個文章列表項目，僅提取最基本的資訊。
    確保列表頁請求極快且穩定。
    """
    try:
        title_tag = item.select_one('.title a')
        meta_tag = item.select_one('.meta')
        push_tag = item.select_one('.nrec span')

        if not (title_tag and title_tag.get('href') and meta_tag):
            return None
            
        if "本文已被刪除" in title_tag.text:
            return None

        article_link = "https://www.ptt.cc" + title_tag['href']
        
        push_count_text = push_tag.get_text(strip=True) if push_tag else ''
        push_count = 0
        if push_count_text:
            if push_count_text == '爆':
                push_count = '爆'
            elif push_count_text.startswith('X'):
                push_count = push_count_text
            else:
                try:
                    push_count = int(push_count_text)
                except (ValueError, TypeError):
                    push_count = 0
        
        return {
            "title": title_tag.text.strip(),
            "link": article_link,
            "board": board,
            "author": meta_tag.select_one('.author').get_text(strip=True) or '',
            "date": meta_tag.select_one('.date').get_text(strip=True) or '',
            "push_count": push_count,
        }
    except Exception as e:
        print(f"錯誤: 處理列表項目時發生未知錯誤: {e}")
        return None

def fetch_ptt_article_list(board, page_url):
    """(快速任務) 抓取 PTT 文章列表頁面。"""
    try:
        response = requests.get(page_url, headers=HEADERS, cookies=COOKIES, timeout=10)
        response.raise_for_status()
    except requests.exceptions.HTTPError as err:
        if err.response.status_code == 404:
            print(f"資訊: 找不到頁面 {page_url}，可能已達看板末頁。")
            return {"articles": [], "prev_page_url": None}
        raise

    soup = BeautifulSoup(response.text, 'html.parser')
    
    articles_tags = soup.select('div.r-ent')
    article_list = []
    
    for item in articles_tags:
        article_data = process_article_item_basic(item, board)
        if article_data:
            article_list.append(article_data)
            
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
    meta_lines = main_content.select('.article-metaline, .article-metaline-right')
    for line in meta_lines:
        tag = line.select_one('.article-meta-tag')
        value = line.select_one('.article-meta-value')
        if tag and value:
            if tag.get_text(strip=True) == '作者': author_full = value.get_text(strip=True)
            elif tag.get_text(strip=True) == '時間': timestamp = value.get_text(strip=True)
        line.decompose()
        
    for p in main_content.select('.push, span.f2, script, style'): p.decompose()
    
    images = [link.get('href') for link in main_content.select('a') if link.get('href') and IMAGE_REGEX.search(link.get('href'))]
    
    for br in main_content.find_all("br"): br.replace_with("\n")
    
    content = main_content.get_text().strip()
    
    return {
        "author_full": author_full, 
        "formatted_timestamp": format_ptt_time(timestamp),
        "content": content,
        "images": list(dict.fromkeys(images)), # 移除重複圖片
    }

def proxy_image_download(proxy_url):
    """代理下載圖片，避免客戶端直接請求時遇到 CORS 或 referer 問題。"""
    try:
        req = requests.get(proxy_url, stream=True, headers=HEADERS, timeout=20)
        req.raise_for_status()
        filename = proxy_url.split('/')[-1].split('?')[0] or 'download'
        return Response(stream_with_context(req.iter_content(chunk_size=8192)),
                        content_type=req.headers.get('content-type'),
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})
    except requests.exceptions.RequestException as e:
        print(f"錯誤: 代理圖片失敗 {proxy_url}: {e}")
        return str(e), 502

# --- API 路由 ---

@app.route('/api/scraper', methods=['GET'])
def scraper_endpoint():
    """
    統一的 API 端點，根據 URL 參數執行不同任務：
    - ?proxy_url=...  : 代理圖片下載
    - ?preview_url=... : 獲取單篇文章預覽
    - ?list_url=...   : 獲取文章列表
    - ?article_url=... : 獲取文章內文
    - ?board=...      : 獲取看板首頁列表
    """
    try:
        if 'proxy_url' in request.args:
            return proxy_image_download(request.args.get('proxy_url'))

        if 'preview_url' in request.args:
            data = get_article_preview_data(request.args.get('preview_url'))
            return jsonify(data)

        board = request.args.get('board', 'Beauty')
        
        if 'list_url' in request.args:
            data = fetch_ptt_article_list(board, request.args.get('list_url'))
            return jsonify(data)
        
        if 'article_url' in request.args:
            data = fetch_ptt_article_content(request.args.get('article_url'))
            return jsonify(data)
        
        # 預設行為：抓取看板首頁
        initial_url = f"https://www.ptt.cc/bbs/{board}/index.html"
        data = fetch_ptt_article_list(board, initial_url)
        return jsonify(data)
            
    except Exception as e:
        print(f"錯誤: 處理請求時發生嚴重錯誤: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
