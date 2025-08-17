from flask import Flask, jsonify, request, Response, stream_with_context
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import locale
# *** FIX: 重新引入並行處理所需的函式庫 ***
from concurrent.futures import ThreadPoolExecutor
from itertools import repeat

app = Flask(__name__)

# 設定時區以正確顯示中文星期
try:
    locale.setlocale(locale.LC_TIME, 'zh_TW.UTF-8')
except locale.Error:
    print("無法設定 zh_TW.UTF-8 locale，將使用預設值。")

# 定義請求標頭和 cookies
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
}
COOKIES = {'over18': '1'}

# 圖片正則表達式
IMAGE_REGEX = re.compile(r'\.(jpg|jpeg|png|gif|avif)$', re.IGNORECASE)

def format_ptt_time(time_str):
    """將 PTT 的英文時間戳轉換為 'YYYY/MM/DD HH:MM 星期X' 格式。"""
    if not time_str:
        return None
    try:
        dt_obj = datetime.strptime(time_str, '%a %b %d %H:%M:%S %Y')
        return dt_obj.strftime('%Y/%m/%d %H:%M %A')
    except (ValueError, TypeError):
        return time_str

# *** FIX: 加回一個更穩定、有錯誤處理的預覽抓取函式 ***
def get_article_preview_data(article_url):
    """
    獲取文章預覽所需的部分資料，包括縮圖、時間和內文摘要。
    增加了超時和錯誤處理，避免單一請求失敗導致整個服務崩潰。
    """
    try:
        response = requests.get(article_url, headers=HEADERS, cookies=COOKIES, timeout=5)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
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
            for link in main_content.select('a'):
                href = link.get('href', '')
                if href and IMAGE_REGEX.search(href):
                    first_image_url = href
                    break
            
            for tag in main_content.select('.article-metaline, .article-metaline-right, .push, .f2, script, style'):
                tag.decompose()
            snippet = main_content.get_text(strip=True)[:100] + "..."

        return {
            "thumbnail": first_image_url,
            "formatted_timestamp": format_ptt_time(timestamp),
            "snippet": snippet
        }
    except Exception as e:
        print(f"獲取預覽失敗 {article_url}: {e}")
        # 如果發生任何錯誤，回傳預設值，確保主程式能繼續運作
        return {"thumbnail": None, "formatted_timestamp": None, "snippet": ""}

def process_article_item(item, board):
    """
    處理單個文章列表項目，包含抓取預覽資訊。
    """
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
    
    # *** FIX: 呼叫函式以獲取縮圖等預覽資訊 ***
    preview_data = get_article_preview_data(article_link)

    base_data = {
        "title": title_tag.text.strip(),
        "link": article_link,
        "board": board,
        "author": meta_tag.select_one('.author').get_text(strip=True) or '',
        "date": meta_tag.select_one('.date').get_text(strip=True) or '',
        "push_count": push_count,
    }
    
    # 合併基本資料和預覽資料
    base_data.update(preview_data)
    return base_data

def fetch_ptt_article_list(board, page_url):
    """抓取 PTT 文章列表頁面 (使用並行處理來加速預覽資訊的獲取)。"""
    try:
        response = requests.get(page_url, headers=HEADERS, cookies=COOKIES, timeout=10)
        response.raise_for_status()
    except requests.exceptions.HTTPError as err:
        if err.response.status_code == 404:
            print(f"找不到頁面 {page_url}，可能已達看板末頁。")
            return {"articles": [], "prev_page_url": None}
        raise

    soup = BeautifulSoup(response.text, 'html.parser')
    
    articles_tags = soup.select('div.r-ent')
    
    # *** FIX: 使用 ThreadPoolExecutor 和 map 來並行處理文章，並保持順序 ***
    with ThreadPoolExecutor(max_workers=10) as executor:
        # executor.map 會依序處理 articles_tags 中的每個項目
        results = executor.map(process_article_item, articles_tags, repeat(board))
        # 過濾掉處理失敗的項目 (回傳 None 的)
        article_list = [r for r in results if r is not None]
            
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
        "images": list(dict.fromkeys(images)),
    }

@app.route('/api/scraper', methods=['GET'])
def scraper_endpoint():
    """API 端點，根據參數決定抓取列表、內文或代理圖片。"""
    try:
        proxy_url = request.args.get('proxy_url')
        if proxy_url:
            try:
                req = requests.get(proxy_url, stream=True, headers=HEADERS, timeout=20)
                req.raise_for_status()
                
                filename = proxy_url.split('/')[-1].split('?')[0] or 'download'
                
                return Response(
                    stream_with_context(req.iter_content(chunk_size=8192)),
                    content_type=req.headers.get('content-type'),
                    headers={'Content-Disposition': f'attachment; filename="{filename}"'}
                )
            except requests.exceptions.RequestException as e:
                print(f"代理圖片失敗 {proxy_url}: {e}")
                return str(e), 502

        board = request.args.get('board', 'Beauty')
        list_url = request.args.get('list_url')
        article_url = request.args.get('article_url')
        
        if list_url:
            data = fetch_ptt_article_list(board, list_url)
            return jsonify(data)
        elif article_url:
            data = fetch_ptt_article_content(article_url)
            return jsonify(data)
        else:
            initial_url = f"https://www.ptt.cc/bbs/{board}/index.html"
            data = fetch_ptt_article_list(board, initial_url)
            return jsonify(data)
            
    except Exception as e:
        print(f"處理請求時發生錯誤: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
