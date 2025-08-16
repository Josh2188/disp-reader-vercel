from flask import Flask, jsonify, request
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

# 定義請求標頭和 Cookies
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
}
COOKIES = {'over18': '1'}

def get_first_image_from_article(article_url):
    """從文章內文中抓取第一張圖片作為縮圖"""
    try:
        response = requests.get(article_url, headers=HEADERS, cookies=COOKIES, timeout=5)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            main_content = soup.select_one('#main-content')
            if main_content:
                image_links = main_content.select('a')
                for link in image_links:
                    href = link.get('href', '')
                    if href.startswith('https://i.imgur.com/') and href.endswith(('.jpg', '.jpeg', '.png', '.gif')):
                        return href
    except Exception as e:
        print(f"抓取縮圖失敗 {article_url}: {e}")
    return None

def fetch_ptt_article_list(board, page_url):
    """抓取 PTT 看板的文章列表"""
    response = requests.get(page_url, headers=HEADERS, cookies=COOKIES, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    
    articles = soup.select('div.r-ent')
    article_list = []
    
    for index, item in enumerate(articles):
        title_tag = item.select_one('.title a')
        meta_tag = item.select_one('.meta')
        
        if title_tag and title_tag.get('href') and meta_tag:
            author = meta_tag.select_one('.author').get_text(strip=True) or ''
            date = meta_tag.select_one('.date').get_text(strip=True) or ''
            article_link = "https://www.ptt.cc" + title_tag['href']
            thumbnail = None
            
            if index < 8: 
                thumbnail = get_first_image_from_article(article_link)

            article_list.append({
                "title": title_tag.text.strip(), "link": article_link, "board": board,
                "author": author, "date": date, "thumbnail": thumbnail
            })
            
    prev_page_link_tag = soup.select_one('a.btn.wide:-soup-contains("上頁")')
    prev_page_url = "https://www.ptt.cc" + prev_page_link_tag['href'] if prev_page_link_tag else None
    return {"articles": article_list, "prev_page_url": prev_page_url}

def fetch_ptt_article_content(article_url):
    """抓取單篇文章的詳細內容"""
    response = requests.get(article_url, headers=HEADERS, cookies=COOKIES, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    main_content = soup.select_one('#main-content')
    if not main_content: raise Exception("找不到 #main-content 區塊。")

    author_full = ''
    timestamp = ''
    meta_lines = main_content.select('.article-metaline')
    for line in meta_lines:
        tag = line.select_one('.article-meta-tag')
        value = line.select_one('.article-meta-value')
        if tag and value:
            if tag.get_text(strip=True) == '作者':
                author_full = value.get_text(strip=True)
            elif tag.get_text(strip=True) == '時間':
                timestamp = value.get_text(strip=True)

    for meta_line in main_content.select('.article-metaline, .article-metaline-right, .push, span.f2'):
        meta_line.decompose()
    for br in main_content.find_all("br"):
        br.replace_with("\n")
        
    content_text = main_content.get_text(strip=True)
    images = [link.get('href', '') for link in main_content.select('a') if link.get('href', '').endswith(('.jpg', '.jpeg', '.png', '.gif'))]
    return {"author_full": author_full, "timestamp": timestamp, "content": content_text, "images": images}

# Vercel 會自動處理 /api/scraper 路由，所以這裡只需要處理根路徑
@app.route('/', methods=['GET'])
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
