블로그 댓글 작성기 프로그램

## 여기어때 숙소 사진 일괄 다운로드 (`yeogi_photos.py`)

숙소 링크만 넣으면 메인 사진을 순서대로 `01.jpg, 02.jpg …`로 저장합니다. (Python 3만 있으면 됨)

```bash
python3 yeogi_photos.py "https://www.yeogi.com/domestic-accommodations/86130?checkIn=2026-10-13&checkOut=2026-10-14&personal=2"
```

- `-o 폴더명` 저장 위치 지정 (기본 `yeogi_<숙소ID>`)
- `--limit 14` 앞에서부터 N장만
- `--list` 다운로드 없이 사진 URL만 출력
- `--browser` 사진을 못 찾으면 실제 브라우저로 열어서 수집 (`pip install playwright && playwright install chromium` 필요)
