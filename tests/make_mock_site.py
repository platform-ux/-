"""자동 점검용 가짜 숙소 페이지 생성기.

메인 갤러리 14장(여러 해상도), 객실 사진, 후기 사진이 들어 있는 페이지를 만든다.
다운로더가 메인 사진 14장만, 확인된 가장 큰 해상도로 받는지 점검하는 데 쓴다.
사용: python tests/make_mock_site.py <폴더>  → <폴더>/domestic-accommodations/86130/index.html
"""
import os
import sys

from PIL import Image

root = sys.argv[1]


def mk(path, size, color):
    path = os.path.join(root, path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, color).save(path)


for i in range(1, 15):
    c = (i * 15, 100, 200 - i * 10)
    mk(f"adimg_new/86130/{i}.png", (1600, 1000), c)
    mk(f"resize_1000x600/adimg_new/86130/{i}.png", (1000, 600), c)
    mk(f"resize_490x348/adimg_new/86130/{i}.png", (490, 348), c)
    mk(f"resize_80x80/adimg_new/86130/{i}.png", (80, 80), c)
for i in range(1, 4):
    mk(f"room/{i}.png", (600, 400), (0, 0, i * 50))
    mk(f"review/{i}.png", (500, 500), (i * 50, 0, 0))

slides = "".join(
    f'<div class="slide" style="display:{"block" if i == 1 else "none"}">'
    f'<img src="/resize_1000x600/adimg_new/86130/{i}.png" '
    f'srcset="/resize_1000x600/adimg_new/86130/{i}.png 1x, /adimg_new/86130/{i}.png 2x" '
    f'style="width:900px;height:560px"></div>' for i in range(1, 15))
thumbs = "".join(f'<img src="/resize_80x80/adimg_new/86130/{i}.png" style="width:60px;height:60px">'
                 for i in range(1, 15))
grid = "".join(f'<img src="/resize_490x348/adimg_new/86130/{i}.png" '
               f'style="width:{"600px;height:400px" if i == 1 else "290px;height:195px"}">' for i in range(1, 6))
html = f"""<!doctype html><html><head><meta charset=utf-8><title>경주 테스트호텔</title></head>
<body style="margin:0"><h1>경주 테스트호텔</h1>
<div id=grid onclick="openG()">{grid}<span>+9</span></div><button onclick="openG()">사진 14장 전체보기</button>
<section style="margin-top:1500px"><h2>객실</h2><img src="/room/1.png" width=600 height=400><img src="/room/2.png" width=600 height=400></section>
<section><h2>후기</h2><img src="/review/1.png" width=500 height=500><img src="/review/3.png" width=500 height=500></section>
<div id=modal style="display:none;position:fixed;inset:0;background:#000">
<div style="color:#fff"><span id=n>1</span> / 14</div>{slides}
<button aria-label="이전 사진" onclick="go(-1)">&lt;</button><button aria-label="다음 사진" onclick="go(1)">&gt;</button>
<div>{thumbs}</div></div>
<script>let cur=1;function openG(){{document.getElementById('modal').style.display='block'}}
function go(d){{const s=document.querySelectorAll('.slide');cur=Math.min(14,Math.max(1,cur+d));
s.forEach((e,i)=>e.style.display=i+1==cur?'block':'none');document.getElementById('n').textContent=cur;}}</script>
</body></html>"""
os.makedirs(os.path.join(root, "domestic-accommodations/86130"), exist_ok=True)
with open(os.path.join(root, "domestic-accommodations/86130/index.html"), "w", encoding="utf-8") as f:
    f.write(html)
print("mock site:", root)
