from flask import Flask, jsonify, request, Response, stream_with_context
import requests
from bs4 import BeautifulSoup
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

app = Flask(__name__)

# Define request headers and cookies
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
}
COOKIES = {'over18': '1'}

IMAGE_REGEX = re.compile(r'\.(jpg|jpeg|png|gif|avif)$', re.IGNORECASE)

def format_ptt_time(time_str):
    """
    Converts PTT's English timestamp to 'YYYY/MM/DD HH:MM 週X' format.
    This version is environment-independent and does not rely on locale.
    """
    if not time_str:
        return None
    try:
        # PTT time format e.g., "Fri Aug 15 04:13:45 2025"
        dt_obj = datetime.strptime(time_str, '%a %b %d %H:%M:%S %Y')
        
        # Manual mapping for weekdays to avoid locale issues
        weekday_map = {
            'Sunday': '週日', 'Monday': '週一', 'Tuesday': '週二', 
            'Wednesday': '週三', 'Thursday': '週四', 'Friday': '週五', 
            'Saturday': '週六'
        }
        
        # Format to YYYY/MM/DD HH:MM and get English weekday name
        formatted_date = dt_obj.strftime('%Y/%m/%d %H:%M')
        english_weekday = dt_obj.strftime('%A')
        
        # Combine and return
        return f"{formatted_date} {weekday_map.get(english_weekday, '')}"
        
    except (ValueError, TypeError):
        # If parsing fails, return the original string
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

def get_article_preview_data(article_url):
    try:
        response = requests.get(article_url, headers=HEADERS, cookies=COOKIES, timeout=5)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        main_content = soup.select_one('#main-content')
        if not main_content: return {"thumbnail": None, "snippet": "", "timestamp": None, "formatted_timestamp": None}

        timestamp = None
        meta_lines = soup.select('.article-metaline, .article-metaline-right')
        for line in meta_lines:
            tag = line.select_one('.article-meta-tag')
            value = line.select_one('.article-meta-value')
            if tag and value and tag.get_text(strip=True) == '時間':
                timestamp = value.get_text(strip=True)
                break
        
        first_image_url, first_youtube_id = None, None
        for link in main_content.select('a'):
            href = link.get('href', '')
            if not href: continue
            if not first_youtube_id:
                youtube_id = extract_youtube_id(href)
                if youtube_id: first_youtube_id = youtube_id
            if not first_image_url and re.search(r'^https?://\S+\.(?:jpg|jpeg|png|gif|avif)$', href, re.IGNORECASE):
                first_image_url = href
            if first_image_url and first_youtube_id: break
        
        thumbnail = f"https://i.ytimg.com/vi/{first_youtube_id}/hqdefault.jpg" if first_youtube_id else first_image_url

        for tag in main_content.select('.article-metaline, .article-metaline-right, .push, .f2'):
            tag.decompose()
        snippet = main_content.get_text(strip=True)[:80] + "..."

        return {
            "thumbnail": thumbnail, 
            "snippet": snippet, 
            "timestamp": timestamp,
            "formatted_timestamp": format_ptt_time(timestamp)
        }
    except Exception as e:
        print(f"Failed to fetch preview for {article_url}: {e}")
        return {"thumbnail": None, "snippet": "", "timestamp": None, "formatted_timestamp": None}

def process_article_item(item, board):
    title_tag = item.select_one('.title a')
    meta_tag = item.select_one('.meta')
    if title_tag and title_tag.get('href') and meta_tag:
        article_link = "https://www.ptt.cc" + title_tag['href']
        preview_data = get_article_preview_data(article_link)
        return {
            "title": title_tag.text.strip(),
            "link": article_link,
            "board": board,
            "author": meta_tag.select_one('.author').get_text(strip=True) or '',
            "date": meta_tag.select_one('.date').get_text(strip=True) or '',
            "timestamp": preview_data.get("timestamp"),
            "formatted_timestamp": preview_data.get("formatted_timestamp"),
            "thumbnail": preview_data.get("thumbnail"),
            "snippet": preview_data.get("snippet")
        }
    return None

def fetch_ptt_article_list(board, page_url):
    response = requests.get(page_url, headers=HEADERS, cookies=COOKIES, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    articles = soup.select('div.r-ent')
    article_list = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_article = {executor.submit(process_article_item, item, board): item for item in articles}
        for future in future_to_article:
            try:
                result = future.result()
                if result: article_list.append(result)
            except Exception as exc:
                print(f'Article processing generated an exception: {exc}')
    prev_page_link_tag = soup.select_one('a.btn.wide:-soup-contains("上頁")')
    prev_page_url = "https://www.ptt.cc" + prev_page_link_tag['href'] if prev_page_link_tag else None
    return {"articles": article_list, "prev_page_url": prev_page_url}

def fetch_ptt_article_content(article_url):
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
    images = [link.get('href') for link in main_content.select('a') if link.get('href') and IMAGE_REGEX.search(link.get('href'))]
    youtube_ids = [yt_id for link in main_content.select('a') if (yt_id := extract_youtube_id(link.get('href')))]
    for br in main_content.find_all("br"): br.replace_with("\n")
    full_text = main_content.get_text()
    content_parts = re.split(r'\n--\n', full_text, 1)
    content = content_parts[0].strip()
    signature = content_parts[1].strip() if len(content_parts) > 1 else ''
    return {
        "author_full": author_full, 
        "timestamp": timestamp, 
        "formatted_timestamp": format_ptt_time(timestamp),
        "content": content,
        "signature": signature, 
        "images": list(dict.fromkeys(images)), 
        "pushes": pushes, 
        "youtube_ids": list(dict.fromkeys(youtube_ids))
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
        print(f"Error processing request: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/proxy-image')
def proxy_image():
    url = request.args.get('url')
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

# This is for local development, Vercel will use its own entry point
if __name__ == '__main__':
    app.run(debug=True)
