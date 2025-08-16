from flask import Flask, jsonify, request
import requests
from bs4 import BeautifulSoup
import re

app = Flask(__name__)

# 定義請求標頭和 Cookies
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
}
COOKIES = {'over18': '1'}

def extract_youtube_id(url):
    """從各種 YouTube 網址格式中提取影片 ID"""
    patterns = [
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?youtu\.be\/([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/embed\/([a-zA-Z0-9_-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def get_article_preview_data(article_url):
    """抓取文章的預覽資料：縮圖 (優先YouTube)、內文預覽"""
    try:
        response = requests.get(article_url, headers=HEADERS, cookies=COOKIES, timeout=5)
        if response.status_code != 200: return {"thumbnail": None, "snippet": ""}
        
        soup = BeautifulSoup(response.text, 'html.parser')
        main_content = soup.select_one('#main-content')
        if not main_content: return {"thumbnail": None, "snippet": ""}

        first_image_url = None
        first_youtube_id = None

        # 尋找第一張圖片和第一個 YouTube 連結
        for link in main_content.select('a'):
            href = link.get('href', '')
            if not first_youtube_id:
                youtube_id = extract_youtube_id(href)
                if youtube_id:
                    first_youtube_id = youtube_id
            
            if not first_image_url and href.startswith('https://i.imgur.com/') and href.endswith(('.jpg', '.jpeg', '.png', '.gif')):
                first_image_url = href
            
            # 如果都找到了就提前結束
            if first_image_url and first_youtube_id:
                break
        
        thumbnail = None
        if first_youtube_id:
            thumbnail = f"https://i.ytimg.com/vi/{first_youtube_id}/hqdefault.jpg"
        elif first_image_url:
            thumbnail = first_image_url

        # 提取內文預覽
        # 移除所有 meta 和 push 標籤以取得乾淨的內文
        for tag in main_content.select('.article-metaline, .article-metaline-right, .push, .f2'):
            tag.decompose()
        snippet = main_content.get_text(strip=True)[:80] + "..."

        return {"thumbnail": thumbnail, "snippet": snippet}

    except Exception as e:
        print(f"抓取預覽失敗 {article_url}: {e}")
        return {"thumbnail": None, "snippet": ""}

def fetch_ptt_article_list(board, page_url):
    """抓取 PTT 看板的文章列表"""
    response = requests.get(page_url, headers=HEADERS, cookies=COOKIES, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    
    articles = soup.select('div.r-ent')
    article_list = []
    
    for item in articles:
        title_tag = item.select_one('.title a')
        meta_tag = item.select_one('.meta')
        
        if title_tag and title_tag.get('href') and meta_tag:
            article_link = "https://www.ptt.cc" + title_tag['href']
            preview_data = get_article_preview_data(article_link)
            
            article_list.append({
                "title": title_tag.text.strip(),
                "link": article_link,
                "board": board,
                "author": meta_tag.select_one('.author').get_text(strip=True) or '',
                "date": meta_tag.select_one('.date').get_text(strip=True) or '',
                "thumbnail": preview_data["thumbnail"],
                "snippet": preview_data["snippet"]
            })
            
    prev_page_link_tag = soup.select_one('a.btn.wide:-soup-contains("上頁")')
    prev_page_url = "https://www.ptt.cc" + prev_page_link_tag['href'] if prev_page_link_tag else None
    return {"articles": article_list, "prev_page_url": prev_page_url}

def fetch_ptt_article_content(article_url):
    """抓取單篇文章的詳細內容，包含內文、簽名檔、推文和YouTube影片"""
    response = requests.get(article_url, headers=HEADERS, cookies=COOKIES, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    main_content = soup.select_one('#main-content')
    if not main_content: raise Exception("找不到 #main-content 區塊。")

    author_full, timestamp = '', ''
    meta_lines = main_content.select('.article-metaline, .article-metaline-right')
    for line in meta_lines:
        tag = line.select_one('.article-meta-tag')
        value = line.select_one('.article-meta-value')
        if tag and value:
            if tag.get_text(strip=True) == '作者': author_full = value.get_text(strip=True)
            elif tag.get_text(strip=True) == '時間': timestamp = value.get_text(strip=True)
        line.decompose()

    pushes = []
    for push in main_content.select('.push'):
        pushes.append({
            "tag": push.select_one('.push-tag').get_text(strip=True) if push.select_one('.push-tag') else '',
            "userid": push.select_one('.push-userid').get_text(strip=True) if push.select_one('.push-userid') else '',
            "content": push.select_one('.push-content').get_text(strip=True) if push.select_one('.push-content') else '',
            "ipdatetime": push.select_one('.push-ipdatetime').get_text(strip=True) if push.select_one('.push-ipdatetime') else ''
        })
        push.decompose()

    for f2_span in main_content.select('span.f2'):
        if '※ 發信站:' in f2_span.get_text() or '※ 編輯:' in f2_span.get_text():
            f2_span.decompose()

    images = [link.get('href', '') for link in main_content.select('a') if link.get('href', '').endswith(('.jpg', '.jpeg', '.png', '.gif'))]
    youtube_ids = [extract_youtube_id(link.get('href', '')) for link in main_content.select('a') if extract_youtube_id(link.get('href', ''))]
    
    for br in main_content.find_all("br"): br.replace_with("\n")
    full_text = main_content.get_text()
    content_parts = re.split(r'\n--\n', full_text, 1)
    content = content_parts[0].strip()
    signature = content_parts[1].strip() if len(content_parts) > 1 else ''

    return {
        "author_full": author_full, "timestamp": timestamp, "content": content,
        "signature": signature, "images": images, "pushes": pushes,
        "youtube_ids": list(dict.fromkeys(youtube_ids)) # 移除重複的 ID
    }

@app.route('/api/scraper', methods=['GET'])
def scraper_endpoint():
    try:
        board = request.args.get('board', 'Gossiping')
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
