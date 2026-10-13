#!/usr/bin/env python3
"""여기어때 숙소 메인 사진 다운로더 (Windows PC + 설치된 Chrome + Playwright)

동작 순서
  1. 내 PC에 설치된 Chrome을 Playwright로 실행해 숙소 페이지를 연다.
  2. 접속이 막히면 브라우저 창을 띄워 사용자가 직접 접속/인증을 마칠 때까지 기다린다.
  3. 메인 갤러리를 열고 사진을 한 장씩 넘기며, 갤러리 안의 실제 <img> 요소에서 주소를 모은다.
     (객실 사진·후기 사진은 갤러리 밖에 있으므로 수집 대상이 아니다.)
  4. 같은 사진의 여러 크기 버전은 페이지 요소(srcset)와 네트워크 응답에서 실제로 확인된
     주소만 후보로 삼아, 내려받아 픽셀 크기가 가장 큰 것을 저장한다.
  5. 다운로드는 브라우저 세션(쿠키 포함)을 그대로 사용한다.

사용 예
  python yeogi_photos.py "https://www.yeogi.com/domestic-accommodations/86130?..." --expected 14
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.parse

try:
    from playwright.sync_api import sync_playwright, Error as PWError, TimeoutError as PWTimeout
except ImportError:
    sys.exit("playwright가 설치되어 있지 않습니다. install.bat을 먼저 실행하세요.\n"
             "  (직접 설치: python -m pip install playwright pillow)")
try:
    from PIL import Image
except ImportError:
    sys.exit("pillow가 설치되어 있지 않습니다. install.bat을 먼저 실행하세요.\n"
             "  (직접 설치: python -m pip install pillow)")

for stream in (sys.stdout, sys.stderr):  # Windows 콘솔에서 한글 깨짐 방지
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(HERE, "chrome_profile")
MIN_SIDE = 150          # 갤러리 안에서 이보다 작게 표시된 이미지는 아이콘/썸네일로 보고 제외
EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif", "AVIF": "avif"}
BLOCK_WORDS = ("access denied", "captcha", "are you a robot", "비정상적인 접근", "접근이 차단",
               "접속이 차단", "보안 문자", "자동입력 방지", "too many requests", "forbidden")


def log(msg=""):
    print(msg, flush=True)


# ---------------------------------------------------------------- 사진 주소 정리
def photo_key(url):
    """같은 사진의 크기별 주소를 하나로 묶기 위한 키 (주소를 만들어내지 않고 비교에만 사용)."""
    p = urllib.parse.urlparse(url)
    if p.path.endswith("/_next/image"):  # Next.js 이미지 프록시: 원본 주소가 url 파라미터에 들어 있음
        inner = urllib.parse.parse_qs(p.query).get("url", [""])[0]
        if inner:
            return photo_key(urllib.parse.urljoin(url, inner))
    segs = [s for s in p.path.split("/")
            if s and not re.fullmatch(r"(resize_[^/]*|\d+x\d+(x\d+)?|w_\d+|h_\d+)", s, re.I)]
    return p.netloc.lower() + "/" + "/".join(segs)


def is_image_url(url):
    return url.startswith("http") and not url.startswith("data:")


def parse_srcset(srcset):
    out = []
    for part in (srcset or "").split(","):
        bits = part.strip().split()
        if bits:
            out.append(bits[0])
    return out


# ---------------------------------------------------------------- 브라우저 내 JS
JS_FIND_GALLERY = r"""
() => {
  // 화면 대부분을 덮는 고정(fixed) 레이어나 dialog 중, 이미지를 담고 있는 가장 위의 것을 갤러리로 본다.
  const vw = innerWidth, vh = innerHeight;
  const cands = [...document.querySelectorAll('[role="dialog"],[aria-modal="true"],body *')].filter(el => {
    if (!el.querySelector('img')) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const isDialog = el.matches('[role="dialog"],[aria-modal="true"]');
    if (!isDialog && cs.position !== 'fixed') return false;
    const r = el.getBoundingClientRect();
    return r.width * r.height > vw * vh * 0.4;
  });
  if (!cands.length) return null;
  const el = cands[cands.length - 1];
  el.setAttribute('data-yeogi-gallery', '1');
  return true;
}
"""

JS_COLLECT = r"""
(root) => {
  const vw = innerWidth, vh = innerHeight;
  const res = [];
  root.querySelectorAll('img').forEach((img, i) => {
    const r = img.getBoundingClientRect();
    const visW = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0));
    const visH = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
    const cs = getComputedStyle(img);
    res.push({
      i, src: img.currentSrc || img.src || '', attrSrc: img.getAttribute('src') || '',
      srcset: img.getAttribute('srcset') || '', dataSrc: img.getAttribute('data-src') || '',
      w: r.width, h: r.height, visArea: (cs.visibility === 'hidden' || cs.opacity === '0') ? 0 : visW * visH,
      nw: img.naturalWidth, nh: img.naturalHeight,
    });
  });
  const text = root.innerText || '';
  const m = text.match(/(\d{1,3})\s*\/\s*(\d{1,3})/);
  return {imgs: res, counter: m ? [parseInt(m[1]), parseInt(m[2])] : null};
}
"""

JS_CLICK_NEXT = r"""
(root) => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 &&
                      getComputedStyle(el).visibility !== 'hidden' && !el.disabled &&
                      el.getAttribute('aria-disabled') !== 'true'; };
  const sels = ['[aria-label*="다음"]','[aria-label*="next" i]','[class*="next" i]','[class*="right" i] button',
                'button[class*="right" i]','[data-testid*="next" i]'];
  for (const s of sels) {
    const el = [...root.querySelectorAll(s)].find(vis);
    if (el) { el.click(); return s; }
  }
  // 이름이 없는 화살표 버튼: 갤러리 오른쪽 절반의 세로 가운데쯤 있는 작은 버튼
  const vw = innerWidth, vh = innerHeight;
  const btn = [...root.querySelectorAll('button,[role="button"]')].filter(vis).find(b => {
    const r = b.getBoundingClientRect();
    return r.left > vw * 0.6 && r.top > vh * 0.2 && r.bottom < vh * 0.8 && r.width < 150 && !b.innerText.trim();
  });
  if (btn) { btn.click(); return 'arrow-button'; }
  return null;
}
"""

JS_OPEN_CANDIDATES = r"""
() => {
  // 페이지 상단에서 갤러리를 여는 요소 후보를 우선순위대로 표시해 둔다.
  document.querySelectorAll('[data-yeogi-open]').forEach(e => e.removeAttribute('data-yeogi-open'));
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const top = el => el.getBoundingClientRect().top + scrollY < 1200;
  const out = [];
  const re = /(사진|이미지|포토).{0,6}(\d+|전체|더\s*보기)|전체\s*(사진|보기)|^\s*\+\s*\d+\s*$|\d+\s*\/\s*\d+/;
  [...document.querySelectorAll('button,a,[role="button"],span,div,p')]
    .filter(el => vis(el) && top(el) && el.children.length <= 3 && re.test((el.innerText || '').trim().slice(0, 30)))
    .slice(0, 5).forEach(el => out.push(el));
  [...document.querySelectorAll('img')]
    .filter(el => vis(el) && top(el))
    .sort((a, b) => { const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                      return rb.width * rb.height - ra.width * ra.height; })
    .slice(0, 2).forEach(el => out.push(el));
  out.forEach((el, i) => el.setAttribute('data-yeogi-open', String(i)));
  return out.length;
}
"""


# ---------------------------------------------------------------- 브라우저 제어
class Session:
    def __init__(self, pw, args):
        self.pw, self.args = pw, args
        self.ctx = self.page = None
        self.headed = False
        self.net = {}   # 네트워크에서 실제로 받은 이미지 응답: url -> content-type

    def launch(self, headed):
        if self.ctx:
            self.ctx.close()
        self.headed = headed
        opts = dict(user_data_dir=PROFILE_DIR, headless=not headed, locale="ko-KR",
                    viewport={"width": 1400, "height": 900}, accept_downloads=False)
        if self.args.chrome_path:
            opts["executable_path"] = self.args.chrome_path
        else:
            opts["channel"] = self.args.channel
        try:
            self.ctx = self.pw.chromium.launch_persistent_context(**opts)
        except PWError as e:
            sys.exit(f"Chrome을 실행하지 못했습니다. Chrome이 설치되어 있는지 확인하세요.\n{e}")
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.ctx.on("response", self._on_response)

    def _on_response(self, resp):
        try:
            ct = resp.headers.get("content-type", "")
            if resp.ok and ct.startswith("image/") and is_image_url(resp.url):
                self.net[resp.url] = ct
        except Exception:
            pass

    def blocked_reason(self, resp):
        if resp is not None and resp.status >= 400:
            return f"HTTP {resp.status}"
        try:
            text = (self.page.title() + " " + self.page.inner_text("body", timeout=5000)[:3000]).lower()
        except Exception:
            return "페이지 내용을 읽을 수 없음"
        for w in BLOCK_WORDS:
            if w in text:
                return f"차단/인증 화면 감지 ('{w}')"
        if not self.page.query_selector("img"):
            return "페이지에 이미지가 없음"
        return None

    def open_page(self, url):
        self.launch(headed=self.args.show)
        for attempt in range(2):
            try:
                resp = self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
                self.page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeout:
                resp = None
            except PWError as e:
                log(f"접속 오류: {e}")
                resp = None
            reason = self.blocked_reason(resp)
            if not reason:
                return
            if attempt == 0 and not self.headed:
                log(f"접속이 막혔습니다: {reason}")
                log("브라우저 창을 띄워서 다시 접속합니다...")
                self.launch(headed=True)
                continue
            break
        self.wait_user(
            "브라우저 창에서 직접 접속/인증(로그인, 보안문자 등)을 완료하고\n"
            "숙소 페이지가 정상적으로 보이면 여기에서 Enter를 누르세요.")

    def wait_user(self, msg):
        if not self.headed:
            raise RuntimeError("headless 상태에서는 사용자 입력을 받을 수 없습니다.")
        log("")
        log("=" * 60)
        log(msg)
        log("=" * 60)
        input("> Enter ")
        # 사용자가 새 탭에서 작업했을 수 있으므로 마지막 탭을 사용
        self.page = self.ctx.pages[-1]
        self.page.bring_to_front()

    def gallery(self):
        self.page.evaluate("() => document.querySelectorAll('[data-yeogi-gallery]')"
                           ".forEach(e => e.removeAttribute('data-yeogi-gallery'))")
        if self.page.evaluate(JS_FIND_GALLERY):
            return self.page.query_selector("[data-yeogi-gallery]")
        return None


# ---------------------------------------------------------------- 갤러리 열기/순회
def open_gallery(s):
    page = s.page
    page.evaluate("() => window.scrollTo(0, 0)")
    n = page.evaluate(JS_OPEN_CANDIDATES)
    start_url = page.url
    for i in range(n):
        el = page.query_selector(f'[data-yeogi-open="{i}"]')
        if not el:
            continue
        try:
            el.scroll_into_view_if_needed(timeout=3000)
            el.click(timeout=5000)
        except Exception:
            continue
        page.wait_for_timeout(1500)
        g = s.gallery()
        if g:
            return g
        if page.url != start_url:      # 갤러리가 별도 페이지로 열린 경우
            page.wait_for_load_state("networkidle", timeout=15000)
            return page.query_selector("body")
    return None


def current_photo(info):
    """화면에 가장 크게 보이는 이미지 = 지금 보고 있는 사진."""
    big = [im for im in info["imgs"] if im["visArea"] > 0 and min(im["w"], im["h"]) >= MIN_SIDE]
    return max(big, key=lambda im: im["visArea"]) if big else None


def walk_gallery(s, g, limit):
    """갤러리를 한 장씩 넘기면서 사진 순서와 실제 주소(src, srcset)를 기록."""
    order, urls = [], {}   # order: photo_key 목록, urls: key -> 확인된 주소 집합

    def add(im):
        found = [u for u in [im["src"], im["attrSrc"], im["dataSrc"], *parse_srcset(im["srcset"])] if u]
        found = [urllib.parse.urljoin(s.page.url, u) for u in found]
        found = [u for u in found if is_image_url(u)]
        if not found:
            return None
        k = photo_key(found[0])
        urls.setdefault(k, set()).update(found)
        if k not in order:
            order.append(k)
        return k

    total, stuck, prev = None, 0, None
    for step in range(limit):
        info = g.evaluate(JS_COLLECT)
        if info["counter"] and info["counter"][1] > 1:
            total = info["counter"][1]
        cur = current_photo(info)
        k = add(cur) if cur else None
        pos = f"{info['counter'][0]}/{info['counter'][1]}" if info["counter"] else f"{step + 1}"
        log(f"  갤러리 {pos}: {'새 사진' if k and k != prev and order[-1] == k else '-'}")
        if total and info["counter"] and info["counter"][0] >= total and len(order) >= total:
            break
        if k == prev or k is None:
            stuck += 1
            if stuck >= 3:
                break
        else:
            stuck = 0
        prev = k
        # 다음 사진으로
        how = g.evaluate(JS_CLICK_NEXT)
        if not how:
            s.page.keyboard.press("ArrowRight")
        for _ in range(20):  # 화면이 바뀔 때까지 최대 3초 대기
            s.page.wait_for_timeout(150)
            nxt = current_photo(g.evaluate(JS_COLLECT))
            if nxt and nxt["src"] != (cur or {}).get("src"):
                break
        s.page.wait_for_timeout(300)
    # 슬라이드가 DOM에 모두 들어 있는 갤러리라면, 넘기면서 놓친 사진을 DOM 순서대로 보충
    info = g.evaluate(JS_COLLECT)
    if not total or len(order) < total:
        for im in info["imgs"]:
            if min(im["w"], im["h"]) >= MIN_SIDE or min(im["nw"], im["nh"]) >= MIN_SIDE * 2:
                add(im)
    return order, urls, total


# ---------------------------------------------------------------- 다운로드
def fetch(s, url, referer):
    r = s.ctx.request.get(url, headers={"Referer": referer}, timeout=30000)
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status}")
    data = r.body()
    img = Image.open(io.BytesIO(data))
    return data, img.size, img.format


def download_all(s, order, urls, outdir, referer):
    os.makedirs(outdir, exist_ok=True)
    # 네트워크 응답에서 확인된 같은 사진의 다른 크기 버전도 후보에 추가
    for u in list(s.net):
        k = photo_key(u)
        if k in urls:
            urls[k].add(u)
    results, hashes = [], set()
    n = 0
    for k in order:
        best, errors = None, []
        for u in sorted(urls[k]):
            try:
                data, (w, h), fmt = fetch(s, u, referer)
            except Exception as e:
                errors.append(f"{u} ({e})")
                continue
            if not best or w * h > best[1] * best[2] or (w * h == best[1] * best[2] and len(data) > len(best[0])):
                best = (data, w, h, fmt, u)
        if best:
            digest = hashlib.sha1(best[0]).hexdigest()
            if digest in hashes:
                log(f"  중복 사진 제외: {best[4]}")
                continue
            hashes.add(digest)
        n += 1
        if not best:
            log(f"  [{n:02d}] 실패: {'; '.join(errors) or '후보 주소 없음'}")
            results.append({"no": n, "ok": False, "candidates": sorted(urls[k]), "errors": errors})
            continue
        data, w, h, fmt, u = best
        name = f"{n:02d}.{EXT.get(fmt, 'jpg')}"
        with open(os.path.join(outdir, name), "wb") as f:
            f.write(data)
        log(f"  [{n:02d}] {name}  {w}x{h}  {len(data) // 1024}KB")
        results.append({"no": n, "ok": True, "file": name, "url": u, "width": w, "height": h,
                        "candidates": sorted(urls[k])})
    return results


def folder_name(s, url):
    m = re.search(r"/(\d+)(?:[/?#]|$)", urllib.parse.urlparse(url).path + "/")
    acc_id = m.group(1) if m else "unknown"
    try:
        title = s.page.evaluate("() => (document.querySelector('h1')?.innerText"
                                " || document.querySelector('meta[property=\"og:title\"]')?.content || '')")
    except Exception:
        title = ""
    title = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", title).strip()[:40].strip(" .")
    return f"{title}_{acc_id}" if title else f"yeogi_{acc_id}"


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="여기어때 숙소 메인 사진 다운로더")
    ap.add_argument("url", nargs="?", help="여기어때 숙소 페이지 주소")
    ap.add_argument("--expected", type=int, help="메인 사진 예상 장수 (예: 14). 수집 수와 다르면 경고")
    ap.add_argument("--out", default=os.path.join(HERE, "downloads"), help="저장할 상위 폴더")
    ap.add_argument("--show", action="store_true", help="처음부터 브라우저 창을 띄워서 실행")
    ap.add_argument("--channel", default="chrome", help="사용할 브라우저 채널 (기본: chrome, msedge 가능)")
    ap.add_argument("--chrome-path", help="Chrome 실행 파일 경로를 직접 지정")
    ap.add_argument("--max-steps", type=int, default=80, help="갤러리 넘기기 최대 횟수")
    a = ap.parse_args()
    url = a.url
    if not url:  # run.bat처럼 주소 없이 실행하면 직접 물어본다
        url = input("여기어때 숙소 주소를 붙여넣고 Enter: ").strip().strip('"')
        if a.expected is None:
            n = input("메인 사진 예상 장수 (모르면 그냥 Enter): ").strip()
            a.expected = int(n) if n.isdigit() else None
    if not url.startswith("http"):
        sys.exit("올바른 주소가 아닙니다.")

    with sync_playwright() as pw:
        s = Session(pw, a)
        log("1) 숙소 페이지 접속 중...")
        s.open_page(url)
        referer = s.page.url

        log("2) 메인 갤러리 여는 중...")
        g = open_gallery(s)
        if not g:
            if not s.headed:
                log("   갤러리를 자동으로 찾지 못했습니다. 브라우저 창을 띄웁니다.")
                s.launch(headed=True)
                s.page.goto(url, wait_until="domcontentloaded")
            s.wait_user("브라우저 창에서 숙소 상단의 메인 사진을 클릭해 갤러리(사진 전체보기)를\n"
                        "직접 열어 주세요. 첫 번째 사진이 보이면 여기에서 Enter를 누르세요.")
            g = s.gallery() or s.page.query_selector("body")

        log("3) 갤러리 사진 순회 중...")
        order, urls, total = walk_gallery(s, g, a.max_steps)
        log(f"   갤러리 표시 장수: {total if total else '확인 안 됨'} / 수집한 사진: {len(order)}장")
        if not order:
            sys.exit("사진을 하나도 수집하지 못했습니다. --show 옵션으로 실행해 화면을 확인해 보세요.")

        outdir = os.path.join(a.out, folder_name(s, url))
        log(f"4) 다운로드 중 → {outdir}")
        results = download_all(s, order, urls, outdir, referer)
        s.ctx.close()

    ok = sum(r["ok"] for r in results)
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"page": url, "gallery_total": total, "collected": len(results),
                   "photos": results}, f, ensure_ascii=False, indent=2)
    log("")
    log("=" * 60)
    log(f"수집한 사진: {len(results)}장" + (f" (갤러리 표시: {total}장)" if total else ""))
    log(f"다운로드 성공: {ok}장 / 실패: {len(results) - ok}장")
    log(f"저장 위치: {outdir}")
    if a.expected and len(results) != a.expected:
        log(f"※ 예상 장수({a.expected}장)와 수집 장수({len(results)}장)가 다릅니다. "
            "--show 옵션으로 실행해 갤러리를 확인해 주세요.")
    log("=" * 60)


if __name__ == "__main__":
    main()
