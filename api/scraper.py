from flask import Flask, jsonify, request, Response, stream_with_context
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime

# Vercel 會尋找名為 'app' 的 Flask 實例
app = Flask(__name__)

# Define request headers and cookies
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
}
COOKIES = {'over18': '1'}

IMAGE_REGEX = re.compile(r'\.(jpg|jpeg|png|gif|avif)$', re.IGNORECASE)

def parse_push_count(push_str):
    """Converts PTT push count string (e.g., '爆', '99', 'X1') to an integer."""
    if not push_str:
        return 0
    push_str = push_str.strip()
    if push_str == '爆':
        return 100
    if push_str.startswith('X'):
        try:
            if push_str == 'XX':
                return -100
            return -1 * int(push_str[1:])
        except (ValueError, IndexError):
            return -1
    try:
        return int(push_str)
    except ValueError:
        return 0

def format_ptt_time(time_str):
    """Converts PTT's English timestamp to 'YYYY/MM/DD HH:MM 週X' format."""
    if not time_str:
        return None
    try:
        dt_obj = datetime.strptime(time_str, '%a %b %d %H:%M:%S %Y')
        weekday_map = {
            'Sunday': '週日', 'Monday': '週一', 'Tuesday': '週二', 
            'Wednesday': '週三', 'Thursday': '週四', 'Friday': '週五', 
            'Saturday': '週六'
        }
        formatted_date = dt_obj.strftime('%Y/%m/%d %H:%M')
        english_weekday = dt_obj.strftime('%A')
        return f"{formatted_date} {weekday_map.get(english_weekday, '')}"
    except (ValueError, TypeError):
        return time_str

def extract_youtube_id(url):
    if not url: return None
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

def process_article_item_for_list(item, board):
    """
    Processes a single article item from the list page.
    This version is optimized for speed and stability on serverless platforms.
    It ONLY parses data available directly on the list page.
    """
    title_tag = item.select_one('.title a')
    meta_tag = item.select_one('.meta')
    push_tag = item.select_one('.nrec span')
    
    if title_tag and title_tag.get('href') and meta_tag:
        push_text = push_tag.get_text(strip=True) if push_tag else ''
        push_count = parse_push_count(push_text)

        return {
            "title": title_tag.text.strip(),
            "link": "https://www.ptt.cc" + title_tag['href'],
            "board": board,
            "author": meta_tag.select_one('.author').get_text(strip=True) or '',
            "date": meta_tag.select_one('.date').get_text(strip=True) or '',
            "push_count": push_count,
            "push_text": push_text,
        }
    return None

def fetch_ptt_article_list(board, page_url):
    """Fetches a list of articles. This is a fast and robust version."""
    response = requests.get(page_url, headers=HEADERS, cookies=COOKIES, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    articles = soup.select('div.r-ent')
    article_list = []
    
    for item in articles:
        result = process_article_item_for_list(item, board)
        if result:
            article_list.append(result)
            
    prev_page_link_tag = soup.select_one('a.btn.wide:-soup-contains("上頁")')
    prev_page_url = "https://www.ptt.cc" + prev_page_link_tag['href'] if prev_page_link_tag else None
    return {"articles": article_list, "prev_page_url": prev_page_url}

def fetch_ptt_article_content(article_url):
    """Fetches the full content of a single article, including images and timestamps."""
    response = requests.get(article_url, headers=HEADERS, cookies=COOKIES, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    main_content = soup.select_one('#main-content')
    if not main_content: raise Exception("Could not find #main-content block.")
    author_full, timestamp = '', ''
    meta_lines = main_content.select('.article-metaline, .article-metaline-right')
    for line in meta_lines:
        tag = line.select_one('.article-meta-tag')
        value = line.select_one('.article-meta-value')
        if tag and value:
            if tag.get_text(strip=True) == '作者': author_full = value.get_text(strip=True)
            elif tag.get_text(strip=True) == '時間': timestamp = value.get_text(strip=True)
        line.decompose()
    pushes = [{"tag": p.select_one('.push-tag').get_text(strip=True) if p.select_one('.push-tag') else '', "userid": p.select_one('.push-userid').get_text(strip=True) if p.select_one('.push-userid') else '', "content": p.select_one('.push-content').get_text(strip=True) if p.select_one('.push-content') else ''} for p in main_content.select('.push')]
    for p in main_content.select('.push'): p.decompose()
    for f2 in main_content.select('span.f2'):
        if '※ 發信站:' in f2.get_text() or '※ 編輯:' in f2.get_text(): f2.decompose()
    
    # Find first image for thumbnail
    first_image_url = None
    for link in main_content.select('a'):
        href = link.get('href', '')
        if href and IMAGE_REGEX.search(href):
            first_image_url = href
            break
    
    images = [link.get('href') for link in main_content.select('a') if link.get('href') and IMAGE_REGEX.search(link.get('href'))]
    youtube_ids = [yt_id for link in main_content.select('a') if (yt_id := extract_youtube_id(link.get('href')))]
    for br in main_content.find_all("br"): br.replace_with("\n")
    full_text = main_content.get_text()
    content_parts = re.split(r'\n--\n', full_text, 1)
    content = content_parts[0].strip()
    signature = content_parts[1].strip() if len(content_parts) > 1 else ''
    
    for tag in main_content.select('.article-metaline, .article-metaline-right, .push, .f2'):
            tag.decompose()
    snippet = main_content.get_text(strip=True)[:80] + "..."

    return {
        "author_full": author_full, 
        "timestamp": timestamp, 
        "formatted_timestamp": format_ptt_time(timestamp),
        "content": content,
        "signature": signature, 
        "images": list(dict.fromkeys(images)), 
        "thumbnail": first_image_url, # Add thumbnail to content data
        "snippet": snippet, # Add snippet to content data
        "pushes": pushes, 
        "youtube_ids": list(dict.fromkeys(youtube_ids))
    }

@app.route('/api/scraper', methods=['GET'])
def handler():
    proxy_url = request.args.get('proxy_url')
    if proxy_url:
        return proxy_image(proxy_url)

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
        print(f"Error processing request: {e}")
        return jsonify({"error": str(e)}), 500

def proxy_image(url):
    if not url: return "Missing URL parameter", 400
    try:
        req = requests.get(url, stream=True, headers=HEADERS, timeout=20)
        req.raise_for_status()
        filename = url.split('/')[-1].split('?')[0] or 'download'
        return Response(
            stream_with_context(req.iter_content(chunk_size=8192)),
            content_type=req.headers.get('content-type'),
            headers={'Content-Disposition': f'attachment; filename="{filename}"'}
        )
    except requests.exceptions.RequestException as e:
        print(f"Error proxying image {url}: {e}")
        return str(e), 502

if __name__ == '__main__':
    app.run(debug=True)
