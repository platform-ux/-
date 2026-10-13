#!/usr/bin/env python3
"""여기어때(yeogi.com) 숙소 링크만 주면 메인 사진을 한 번에 내려받는 스크립트.

사용법:
    python3 yeogi_photos.py "https://www.yeogi.com/domestic-accommodations/86130?..."
    python3 yeogi_photos.py URL -o 경주숙소          # 저장 폴더 지정
    python3 yeogi_photos.py URL --limit 14           # 앞에서부터 N장만
    python3 yeogi_photos.py URL --browser            # 실제 브라우저로 열어서 수집 (playwright 필요)

추가 설치 없이 파이썬 표준 라이브러리만으로 동작합니다.
"""
import argparse
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
IMG_HOSTS = ("image.goodchoice.kr", "yaimg.yanolja.com", "img.yeogi.com",
             "image.yeogi.com", "static.yeogi.com")
IMG_EXT = re.compile(r"\.(jpe?g|png|webp|gif)(\?|$)", re.I)
URL_RE = re.compile(r"https?:(?://|\\/\\/|\\u002F\\u002F)[^\s\"'<>()\\]+(?:\\/[^\s\"'<>()\\]+)*", re.I)
# image.goodchoice.kr/resize_490x348/adimg_new/... → 원본 해상도 경로
RESIZE_RE = re.compile(r"(image\.goodchoice\.kr)/resize_[^/]+/")


def fetch(url, referer=None):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "ko-KR,ko;q=0.9",
        **({"Referer": referer} if referer else {}),
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def clean(u):
    u = u.replace("\\u002F", "/").replace("\\/", "/").replace("\\u0026", "&")
    u = html.unescape(u).rstrip(",;")
    if u.startswith("//"):
        u = "https:" + u
    return u


def is_photo(u):
    host = urllib.parse.urlparse(u).netloc
    return any(host.endswith(h) for h in IMG_HOSTS) and bool(IMG_EXT.search(u))


def photo_key(u):
    """같은 사진의 다른 크기 버전을 하나로 묶기 위한 키."""
    return RESIZE_RE.sub(r"\1/", u).split("?")[0]


def walk(node, found):
    """JSON 안에서 '이미지 URL 배열'을 모두 찾아 리스트로 모은다."""
    if isinstance(node, list):
        urls = []
        for item in node:
            if isinstance(item, str) and is_photo(clean(item)):
                urls.append(clean(item))
            elif isinstance(item, dict):
                for v in item.values():
                    if isinstance(v, str) and is_photo(clean(v)):
                        urls.append(clean(v))
                        break
        if len(urls) >= 2:
            found.append(urls)
        for item in node:
            walk(item, found)
    elif isinstance(node, dict):
        for v in node.values():
            walk(v, found)


def dedupe(urls):
    seen, out = set(), []
    for u in urls:
        k = photo_key(u)
        if k not in seen:
            seen.add(k)
            out.append(u)
    return out


def extract_from_html(page, acc_id):
    # 1) Next.js 페이지 데이터(__NEXT_DATA__)에서 가장 큰 사진 배열 = 메인 갤러리
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if m:
        try:
            groups = []
            walk(json.loads(m.group(1)), groups)
            if groups:
                groups = [dedupe(g) for g in groups]
                # 숙소 ID가 경로에 들어간 배열을 우선, 그다음 길이 순
                groups.sort(key=lambda g: (sum(acc_id in u for u in g), len(g)), reverse=True)
                return groups[0]
        except ValueError:
            pass

    # 2) 그 외(App Router 스트리밍 데이터 등): HTML 전체에서 이미지 URL을 등장 순서대로 수집
    urls = [clean(u) for u in URL_RE.findall(page)]
    urls = [u for u in urls if is_photo(u)]
    own = [u for u in urls if acc_id and acc_id in u]
    return dedupe(own or urls)


def extract_with_browser(url, acc_id):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("--browser 모드는 playwright가 필요합니다:\n"
                 "  pip install playwright && playwright install chromium")
    seen = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(user_agent=UA, locale="ko-KR")
        pg.on("response", lambda r: is_photo(r.url) and seen.append(r.url))
        pg.goto(url, wait_until="networkidle")
        page = pg.content()
        b.close()
    urls = extract_from_html(page, acc_id)
    if not urls:
        own = [u for u in seen if acc_id in u]
        urls = dedupe(own or seen)
    return urls


def download(urls, outdir, referer):
    os.makedirs(outdir, exist_ok=True)
    ok = 0
    for i, u in enumerate(urls, 1):
        candidates = [RESIZE_RE.sub(r"\1/", u), u] if RESIZE_RE.search(u) else [u]
        for cand in candidates:  # 원본 해상도 먼저 시도, 실패 시 원래 URL
            try:
                data = fetch(cand, referer)
                break
            except Exception as e:
                err = e
        else:
            print(f"  [{i:02d}] 실패: {u} ({err})")
            continue
        ext = (IMG_EXT.search(cand).group(1).lower() if IMG_EXT.search(cand) else "jpg")
        path = os.path.join(outdir, f"{i:02d}.{ext.replace('jpeg', 'jpg')}")
        with open(path, "wb") as f:
            f.write(data)
        ok += 1
        print(f"  [{i:02d}] {path} ({len(data) // 1024}KB)")
    return ok


def main():
    ap = argparse.ArgumentParser(description="여기어때 숙소 사진 일괄 다운로드")
    ap.add_argument("url", help="여기어때 숙소 페이지 링크")
    ap.add_argument("-o", "--out", help="저장 폴더 (기본: yeogi_<숙소ID>)")
    ap.add_argument("--limit", type=int, help="앞에서부터 N장만 받기")
    ap.add_argument("--browser", action="store_true", help="실제 브라우저(playwright)로 수집")
    ap.add_argument("--list", action="store_true", help="다운로드 없이 URL만 출력")
    a = ap.parse_args()

    m = re.search(r"/(?:domestic-accommodations|accommodations?)/(\d+)", a.url)
    acc_id = m.group(1) if m else ""
    print(f"숙소 ID: {acc_id or '알 수 없음'}")

    if a.browser:
        urls = extract_with_browser(a.url, acc_id)
    else:
        urls = extract_from_html(fetch(a.url).decode("utf-8", "replace"), acc_id)
        if not urls:
            print("페이지 데이터에서 사진을 못 찾았습니다. --browser 옵션으로 다시 시도해 보세요.")
            sys.exit(1)

    if a.limit:
        urls = urls[:a.limit]
    print(f"사진 {len(urls)}장 발견")
    if a.list:
        print("\n".join(urls))
        return

    outdir = a.out or f"yeogi_{acc_id or 'photos'}"
    n = download(urls, outdir, a.url)
    print(f"완료: {n}/{len(urls)}장 → {os.path.abspath(outdir)}")


if __name__ == "__main__":
    main()
