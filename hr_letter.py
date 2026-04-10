"""
인재경영실 Letter — Notion 기반 뉴스레터 시스템
================================================
노션 페이지 작성 → GitHub Actions 수동 실행 → 이메일 발송 + GitHub Pages 누적 아카이빙

필수 GitHub Secrets:
  - NOTION_TOKEN       : Notion Integration 토큰
  - GMAIL_USER         : 발신 Gmail 주소
  - GMAIL_APP_PASS     : Gmail 앱 비밀번호 (16자리)
  - EMAIL_RECIPIENTS   : 수신자 이메일 (쉼표 구분)

워크플로우 실행 시 입력:
  - notion_page_id     : 발행할 노션 페이지 URL 또는 ID
  - send_email         : true / false (기본: true)
"""

import os, re, json, base64, smtplib, uuid
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests

# ──────────────────────────────────────────────────────────────
# 상수 · 설정
# ──────────────────────────────────────────────────────────────
KST             = timezone(timedelta(hours=9))
ARCHIVE_FILE    = "letters_archive.json"
INDEX_FILE      = "index.html"
NEWSLETTER_NAME = "인재경영실 Letter"
ORG_NAME        = "상상인그룹 인재경영실"
TEAL            = "#00A7A7"
DARK_NAVY       = "#1e2235"
NOTION_API_VER  = "2022-06-28"


# ──────────────────────────────────────────────────────────────
# Notion 페이지 ID 정규화
# ──────────────────────────────────────────────────────────────

def normalize_page_id(raw: str) -> str:
    """URL 또는 ID 문자열에서 순수 32자리 hex ID 추출"""
    raw = raw.strip()
    # URL 형식이면 마지막 path segment 추출
    if raw.startswith("http"):
        raw = raw.rstrip("/").split("/")[-1].split("?")[0]
    # 제목-ID 형식 (page-title-abc123...) → 마지막 32자 추출
    raw = re.sub(r"[^a-fA-F0-9]", "", raw)
    if len(raw) >= 32:
        return raw[-32:]
    return raw


# ──────────────────────────────────────────────────────────────
# Notion API
# ──────────────────────────────────────────────────────────────

def notion_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_API_VER,
        "Content-Type": "application/json",
    }

def fetch_page_meta(page_id: str, token: str) -> dict:
    url = f"https://api.notion.com/v1/pages/{page_id}"
    r = requests.get(url, headers=notion_headers(token), timeout=15)
    r.raise_for_status()
    return r.json()

def fetch_blocks(block_id: str, token: str) -> list:
    """페이지네이션을 지원하는 블록 전체 로드"""
    blocks, cursor = [], None
    while True:
        url = f"https://api.notion.com/v1/blocks/{block_id}/children"
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = requests.get(url, headers=notion_headers(token), params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        blocks.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return blocks

def get_page_title(page_data: dict) -> str:
    """페이지 properties에서 title 추출"""
    props = page_data.get("properties", {})
    for key in props:
        prop = props[key]
        if prop.get("type") == "title":
            return "".join(rt.get("plain_text", "") for rt in prop.get("title", []))
    return NEWSLETTER_NAME


# ──────────────────────────────────────────────────────────────
# Notion 블록 → HTML 변환
# ──────────────────────────────────────────────────────────────

def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def rt_to_html(rich_texts: list) -> str:
    """Notion rich_text 배열을 인라인 HTML로 변환"""
    out = ""
    for rt in rich_texts:
        t = esc(rt.get("plain_text", ""))
        a = rt.get("annotations", {})
        h = rt.get("href")
        if a.get("bold"):          t = f"<strong>{t}</strong>"
        if a.get("italic"):        t = f"<em>{t}</em>"
        if a.get("underline"):     t = f"<u>{t}</u>"
        if a.get("strikethrough"): t = f"<s>{t}</s>"
        if a.get("code"):
            t = (f'<code style="background:#e6f7f7;color:{TEAL};padding:2px 6px;'
                 f'border-radius:3px;font-size:88%;font-family:monospace;">{t}</code>')
        if h:
            t = (f'<a href="{h}" target="_blank" rel="noopener"'
                 f' style="color:{TEAL};text-decoration:underline;">{t}</a>')
        out += t
    return out

def blocks_to_html(blocks: list):
    """
    Notion 블록 목록 → (이메일용 HTML 문자열, 발췌 문자열)
    이메일 클라이언트 호환을 위해 table·인라인 스타일 사용
    """
    html, excerpt = "", ""
    i = 0

    while i < len(blocks):
        b  = blocks[i]
        bt = b.get("type", "")

        # ── paragraph ──
        if bt == "paragraph":
            rts   = b["paragraph"].get("rich_text", [])
            text  = rt_to_html(rts)
            plain = "".join(r.get("plain_text", "") for r in rts)
            if not excerpt and plain.strip():
                excerpt = plain.strip()[:160]
            if text.strip():
                html += (f'<p style="margin:0 0 16px;line-height:1.8;'
                         f'color:#374151;font-size:15px;">{text}</p>')
            else:
                html += '<div style="height:8px;"></div>'

        # ── headings ──
        elif bt == "heading_1":
            t = rt_to_html(b["heading_1"].get("rich_text", []))
            html += (f'<h1 style="font-size:21px;font-weight:800;color:{DARK_NAVY};'
                     f'margin:32px 0 14px;padding-bottom:10px;'
                     f'border-bottom:2px solid {TEAL};">{t}</h1>')
        elif bt == "heading_2":
            t = rt_to_html(b["heading_2"].get("rich_text", []))
            html += (f'<h2 style="font-size:17px;font-weight:700;color:{DARK_NAVY};'
                     f'margin:26px 0 10px;">{t}</h2>')
        elif bt == "heading_3":
            t = rt_to_html(b["heading_3"].get("rich_text", []))
            html += (f'<h3 style="font-size:13px;font-weight:700;color:{TEAL};'
                     f'margin:20px 0 8px;text-transform:uppercase;letter-spacing:0.6px;">{t}</h3>')

        # ── bulleted list (연속 항목 묶음) ──
        elif bt == "bulleted_list_item":
            items = ""
            while i < len(blocks) and blocks[i].get("type") == "bulleted_list_item":
                t = rt_to_html(blocks[i]["bulleted_list_item"].get("rich_text", []))
                items += f'<li style="margin-bottom:8px;line-height:1.7;color:#374151;">{t}</li>'
                i += 1
            html += f'<ul style="margin:0 0 16px;padding-left:22px;">{items}</ul>'
            continue

        # ── numbered list (연속 항목 묶음) ──
        elif bt == "numbered_list_item":
            items = ""
            while i < len(blocks) and blocks[i].get("type") == "numbered_list_item":
                t = rt_to_html(blocks[i]["numbered_list_item"].get("rich_text", []))
                items += f'<li style="margin-bottom:8px;line-height:1.7;color:#374151;">{t}</li>'
                i += 1
            html += f'<ol style="margin:0 0 16px;padding-left:22px;">{items}</ol>'
            continue

        # ── divider ──
        elif bt == "divider":
            html += f'<hr style="border:none;border-top:2px solid #e5e7eb;margin:28px 0;">'

        # ── callout (table로 이메일 호환) ──
        elif bt == "callout":
            icon_d = b["callout"].get("icon") or {}
            icon   = icon_d.get("emoji", "💡") if icon_d.get("type") == "emoji" else "💡"
            t      = rt_to_html(b["callout"].get("rich_text", []))
            html  += (
                f'<table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 16px;">'
                f'<tr><td style="background:#e6f7f7;border-left:4px solid {TEAL};'
                f'border-radius:0 8px 8px 0;padding:14px 18px;line-height:1.75;'
                f'color:#374151;font-size:15px;">{icon}&nbsp;&nbsp;{t}</td></tr></table>'
            )

        # ── quote ──
        elif bt == "quote":
            t = rt_to_html(b["quote"].get("rich_text", []))
            html += (
                f'<table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 16px;">'
                f'<tr><td style="background:#f9fafb;border-left:4px solid {TEAL};'
                f'padding:14px 20px;color:#6b7280;font-style:italic;'
                f'line-height:1.75;font-size:15px;border-radius:0 8px 8px 0;">{t}</td></tr></table>'
            )

        # ── image ──
        elif bt == "image":
            img = b["image"]
            src = ((img.get("file") or {}).get("url")
                   or (img.get("external") or {}).get("url", ""))
            cap = "".join(r.get("plain_text", "") for r in img.get("caption", []))
            if src:
                html += (
                    f'<div style="text-align:center;margin:0 0 16px;">'
                    f'<img src="{src}" alt="{esc(cap)}" width="100%"'
                    f' style="max-width:580px;border-radius:10px;'
                    f'box-shadow:0 2px 12px rgba(0,0,0,0.08);">'
                    + (f'<p style="font-size:12px;color:#9ca3af;margin:6px 0 0;">{esc(cap)}</p>' if cap else "")
                    + '</div>'
                )

        # ── toggle (제목만 표시) ──
        elif bt == "toggle":
            t = rt_to_html(b["toggle"].get("rich_text", []))
            html += (
                f'<div style="background:#f9fafb;border:1px solid #e5e7eb;'
                f'border-radius:8px;padding:14px 18px;margin:0 0 14px;">'
                f'<strong style="color:{DARK_NAVY};">▶ {t}</strong></div>'
            )

        # ── code ──
        elif bt == "code":
            code_text = "".join(r.get("plain_text", "") for r in b["code"].get("rich_text", []))
            html += (
                f'<pre style="background:{DARK_NAVY};color:#e5e7eb;border-radius:10px;'
                f'padding:18px 22px;margin:0 0 16px;overflow-x:auto;'
                f'font-size:13px;line-height:1.6;font-family:\'Courier New\',monospace;">'
                f'{esc(code_text)}</pre>'
            )

        i += 1

    return html, excerpt


# ──────────────────────────────────────────────────────────────
# 이메일 HTML 빌더
# ──────────────────────────────────────────────────────────────

def build_email_html(title: str, content_html: str, date_str: str,
                     letter_no: int, archive_url: str) -> str:

    # 로고 마크 (이메일 안전 테이블 레이아웃)
    logo_html = f"""
<table cellpadding="0" cellspacing="0" role="presentation" style="margin:0 auto 18px;">
  <tr>
    <td style="padding-right:10px;vertical-align:middle;">
      <!-- 로고 심볼 (두 원) -->
      <table cellpadding="0" cellspacing="0" role="presentation">
        <tr>
          <td>
            <div style="width:20px;height:20px;background:{TEAL};border-radius:50%;"></div>
          </td>
          <td style="padding-left:4px;padding-top:10px;">
            <div style="width:15px;height:15px;background:{TEAL};border-radius:50%;opacity:0.65;"></div>
          </td>
        </tr>
      </table>
    </td>
    <td style="vertical-align:middle;">
      <div style="font-size:11px;color:#94a3b8;font-weight:500;line-height:1.2;">상상인그룹</div>
      <div style="font-size:16px;font-weight:800;color:#ffffff;line-height:1.2;letter-spacing:-0.2px;">인재경영실</div>
    </td>
  </tr>
</table>"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{NEWSLETTER_NAME} — {esc(title)}</title>
</head>
<body style="margin:0;padding:0;background:#f0f2f5;
  font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',
  'Apple Color Emoji',sans-serif;">

<div style="max-width:660px;margin:0 auto;padding:32px 16px 48px;">

  <!-- ① 헤더 (다크 네이비 + 티얼 포인트) -->
  <div style="background:{DARK_NAVY};border-radius:16px 16px 0 0;
    padding:32px 36px 28px;text-align:center;position:relative;overflow:hidden;">

    <!-- 배경 장식 원 -->
    <div style="position:absolute;top:-50px;right:-50px;width:160px;height:160px;
      background:{TEAL};border-radius:50%;opacity:0.09;"></div>
    <div style="position:absolute;bottom:-30px;left:-30px;width:100px;height:100px;
      background:{TEAL};border-radius:50%;opacity:0.07;"></div>

    {logo_html}

    <!-- 구분선 -->
    <div style="width:44px;height:2px;background:{TEAL};margin:0 auto 18px;"></div>

    <!-- 뉴스레터명 -->
    <div style="font-size:24px;font-weight:800;color:#ffffff;
      letter-spacing:-0.4px;margin-bottom:8px;">
      {NEWSLETTER_NAME}
    </div>

    <!-- 날짜 · 호수 -->
    <div style="font-size:13px;color:#94a3b8;">
      {date_str} &nbsp;·&nbsp; <strong style="color:{TEAL};">No.{letter_no}</strong>
    </div>
  </div>

  <!-- ② 제목 배너 (티얼) -->
  <div style="background:{TEAL};padding:18px 36px;">
    <div style="font-size:18px;font-weight:700;color:#ffffff;
      line-height:1.45;letter-spacing:-0.2px;">
      {esc(title)}
    </div>
  </div>

  <!-- ③ 본문 -->
  <div style="background:#ffffff;padding:36px 36px 32px;">
    {content_html}
  </div>

  <!-- ④ 그라디언트 바 -->
  <div style="height:4px;background:linear-gradient(90deg,{TEAL} 0%,{DARK_NAVY} 100%);"></div>

  <!-- ⑤ 푸터 -->
  <div style="background:#f8fafc;border:1px solid #e2e8f0;border-top:none;
    border-radius:0 0 16px 16px;padding:20px 36px;text-align:center;">

    <div style="margin-bottom:10px;">
      <a href="{archive_url}" target="_blank" rel="noopener"
        style="color:{TEAL};text-decoration:none;font-size:13px;font-weight:600;">
        📂 인재경영실 Letter 아카이브
      </a>
      &nbsp;&nbsp;|&nbsp;&nbsp;
      <span style="color:#94a3b8;font-size:13px;">상상인그룹 인재경영실</span>
    </div>

    <div style="font-size:12px;color:#cbd5e1;margin-top:4px;">
      이 메일은 인재경영실 Letter 구독자에게 발송됩니다.
    </div>
  </div>

</div>
</body>
</html>"""


# ──────────────────────────────────────────────────────────────
# GitHub Pages HTML 생성 (누적 아카이브, 날짜 구분 없음)
# ──────────────────────────────────────────────────────────────

def build_index_html() -> str:
    return """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>인재경영실 Letter — 아카이브</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: 'Apple SD Gothic Neo', 'Noto Sans KR', 'Malgun Gothic', sans-serif;
      background: #f0f2f5; color: #1e2235; min-height: 100vh;
    }

    /* ─── 헤더 ─── */
    .site-header { background: #1e2235; }
    .header-inner {
      max-width: 900px; margin: 0 auto;
      padding: 28px 24px 24px;
      display: flex; align-items: center; justify-content: space-between;
      flex-wrap: wrap; gap: 12px;
    }
    .logo-area { display: flex; align-items: center; gap: 12px; }
    .logo-mark { width: 34px; height: 34px; position: relative; flex-shrink: 0; }
    .logo-mark .c1 {
      position: absolute; top: 0; left: 0;
      width: 22px; height: 22px; background: #00A7A7; border-radius: 50%;
    }
    .logo-mark .c2 {
      position: absolute; bottom: 0; right: 0;
      width: 15px; height: 15px; background: #00A7A7;
      border-radius: 50%; opacity: 0.65;
    }
    .logo-text .org  { font-size: 11px; color: #94a3b8; font-weight: 500; }
    .logo-text .name { font-size: 18px; font-weight: 800; color: #fff; letter-spacing: -0.3px; }
    .header-badge {
      background: #00A7A7; color: #fff; font-size: 11px;
      font-weight: 700; padding: 4px 10px; border-radius: 20px; letter-spacing: 0.3px;
    }

    /* ─── 검색 바 ─── */
    .search-bar { background: #fff; border-bottom: 1px solid #e2e8f0; }
    .search-inner {
      max-width: 900px; margin: 0 auto; padding: 14px 24px;
      display: flex; gap: 12px; align-items: center;
    }
    .search-inner input {
      flex: 1; border: 1.5px solid #e2e8f0; border-radius: 9px;
      padding: 9px 16px; font-size: 14px; outline: none;
      font-family: inherit; transition: border-color .2s; color: #374151;
    }
    .search-inner input:focus { border-color: #00A7A7; }
    .count-badge { font-size: 13px; color: #64748b; white-space: nowrap; }
    .count-badge strong { color: #00A7A7; }

    /* ─── 메인 ─── */
    .main { max-width: 900px; margin: 0 auto; padding: 32px 24px; }

    /* ─── 카드 그리드 ─── */
    .letters-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
      gap: 22px;
    }

    /* ─── 카드 ─── */
    .card {
      background: #fff; border-radius: 14px;
      border: 1px solid #e2e8f0; overflow: hidden;
      transition: transform .2s ease, box-shadow .2s ease;
    }
    .card:hover { transform: translateY(-3px); box-shadow: 0 12px 32px rgba(0,0,0,0.09); }

    .card-header {
      background: #1e2235; padding: 16px 22px;
      display: flex; justify-content: space-between; align-items: center;
    }
    .card-no {
      font-size: 11px; color: #00A7A7; font-weight: 700;
      letter-spacing: 0.6px; text-transform: uppercase;
    }
    .card-date { font-size: 12px; color: #64748b; }

    .card-body { padding: 18px 22px 14px; }
    .card-title {
      font-size: 16px; font-weight: 700; color: #1e2235;
      line-height: 1.45; margin-bottom: 10px;
    }
    .card-excerpt {
      font-size: 13px; color: #64748b; line-height: 1.65;
      display: -webkit-box; -webkit-line-clamp: 3;
      -webkit-box-orient: vertical; overflow: hidden;
    }

    .card-footer { padding: 12px 22px 18px; display: flex; gap: 10px; }
    .btn-read {
      background: #00A7A7; color: #fff; border: none;
      border-radius: 8px; padding: 8px 18px; font-size: 13px;
      font-weight: 600; cursor: pointer; font-family: inherit;
      transition: background .15s;
    }
    .btn-read:hover { background: #008f8f; }
    .btn-collapse {
      background: #f1f5f9; color: #64748b; border: none;
      border-radius: 8px; padding: 8px 14px; font-size: 13px;
      cursor: pointer; font-family: inherit; display: none;
      transition: background .15s;
    }
    .btn-collapse:hover { background: #e2e8f0; }

    /* 전문 영역 */
    .card-full {
      display: none; border-top: 3px solid #00A7A7;
      padding: 28px 28px 24px; background: #fff;
    }
    .card-full h1 {
      font-size: 20px; font-weight: 800; color: #1e2235;
      margin: 28px 0 12px; padding-bottom: 8px;
      border-bottom: 2px solid #00A7A7;
    }
    .card-full h2 { font-size: 17px; font-weight: 700; color: #1e2235; margin: 24px 0 10px; }
    .card-full h3 {
      font-size: 13px; font-weight: 700; color: #00A7A7;
      margin: 18px 0 8px; text-transform: uppercase; letter-spacing: .6px;
    }
    .card-full p  { margin: 0 0 16px; line-height: 1.8; color: #374151; font-size: 15px; }
    .card-full ul { margin: 0 0 16px; padding-left: 22px; }
    .card-full ol { margin: 0 0 16px; padding-left: 22px; }
    .card-full li { margin-bottom: 8px; line-height: 1.7; color: #374151; }
    .card-full hr { border: none; border-top: 2px solid #e5e7eb; margin: 28px 0; }
    .card-full blockquote {
      margin: 0 0 16px; padding: 14px 20px;
      border-left: 4px solid #00A7A7; background: #f9fafb;
      color: #6b7280; font-style: italic; border-radius: 0 8px 8px 0; line-height: 1.75;
    }
    .card-full table { width: 100%; margin-bottom: 16px; }
    .card-full pre {
      background: #1e2235; color: #e5e7eb; border-radius: 10px;
      padding: 18px 22px; margin: 0 0 16px; overflow-x: auto;
      font-size: 13px; line-height: 1.6; font-family: 'Courier New', monospace;
    }
    .card-full img { max-width: 100%; border-radius: 10px; }
    .card-full code {
      background: #e6f7f7; color: #00A7A7; padding: 2px 6px;
      border-radius: 3px; font-size: 88%; font-family: monospace;
    }

    /* 빈 상태 */
    .empty-state { text-align: center; padding: 80px 24px; color: #94a3b8; }
    .empty-state .emoji { font-size: 48px; margin-bottom: 16px; }

    /* 사이트 푸터 */
    .site-footer { text-align: center; padding: 32px 24px; color: #94a3b8; font-size: 12px; }
    .site-footer a { color: #00A7A7; text-decoration: none; }

    @media (max-width: 640px) {
      .letters-grid { grid-template-columns: 1fr; }
      .header-inner { flex-direction: column; align-items: flex-start; }
      .card-full { padding: 20px 18px; }
    }
  </style>
</head>
<body>

<!-- 헤더 -->
<header class="site-header">
  <div class="header-inner">
    <div class="logo-area">
      <div class="logo-mark"><div class="c1"></div><div class="c2"></div></div>
      <div class="logo-text">
        <div class="org">상상인그룹</div>
        <div class="name">인재경영실 Letter</div>
      </div>
    </div>
    <span class="header-badge">ARCHIVE</span>
  </div>
</header>

<!-- 검색 바 -->
<div class="search-bar">
  <div class="search-inner">
    <input type="text" id="searchInput" placeholder="레터 제목 또는 내용으로 검색…" autocomplete="off">
    <div class="count-badge">총 <strong id="letterCount">-</strong>편</div>
  </div>
</div>

<!-- 메인 -->
<main class="main">
  <div class="letters-grid" id="grid"></div>
  <div class="empty-state" id="emptyState" style="display:none;">
    <div class="emoji">📭</div>
    <p>아직 발행된 레터가 없거나 검색 결과가 없습니다.</p>
  </div>
</main>

<!-- 푸터 -->
<footer class="site-footer">
  <p>© 상상인그룹 인재경영실 &nbsp;·&nbsp;
    <a href="https://www.sangsangin.com" target="_blank" rel="noopener">sangsangin.com</a>
  </p>
</footer>

<script>
let allLetters = [];

async function loadData() {
  try {
    const r = await fetch("letters_archive.json?t=" + Date.now());
    if (!r.ok) throw new Error("fetch failed");
    allLetters = await r.json();
  } catch(e) {
    allLetters = [];
  }
  renderGrid(allLetters);
}

function formatDate(dateStr) {
  const d = new Date(dateStr + "T00:00:00+09:00");
  return d.toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric" });
}

function renderGrid(letters) {
  const grid   = document.getElementById("grid");
  const empty  = document.getElementById("emptyState");
  document.getElementById("letterCount").textContent = letters.length;

  if (!letters.length) {
    grid.innerHTML = "";
    empty.style.display = "block";
    return;
  }
  empty.style.display = "none";

  grid.innerHTML = letters.map((l, idx) => `
    <article class="card">
      <div class="card-header">
        <div class="card-no">No.${l.number || (letters.length - idx)}</div>
        <div class="card-date">${formatDate(l.date)}</div>
      </div>
      <div class="card-body">
        <div class="card-title">${escHtml(l.title)}</div>
        <div class="card-excerpt">${escHtml(l.excerpt || "")}</div>
      </div>
      <div class="card-footer">
        <button class="btn-read" onclick="expandCard(this, ${idx})">전문 읽기 ↓</button>
        <button class="btn-collapse" onclick="collapseCard(this, ${idx})">접기 ↑</button>
      </div>
      <div class="card-full" id="full-${idx}"></div>
    </article>
  `).join("");
}

function escHtml(s) {
  return String(s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function expandCard(btn, idx) {
  const full = document.getElementById("full-" + idx);
  // 콘텐츠 지연 주입 (XSS 안전: 서버 생성 HTML만 사용)
  if (!full.dataset.loaded) {
    full.innerHTML = allLetters[idx].html_content || "<p>본문을 불러올 수 없습니다.</p>";
    full.dataset.loaded = "1";
  }
  full.style.display = "block";
  btn.style.display  = "none";
  btn.nextElementSibling.style.display = "inline-flex";
  full.scrollIntoView({ behavior: "smooth", block: "start" });
}

function collapseCard(btn, idx) {
  const full = document.getElementById("full-" + idx);
  full.style.display = "none";
  btn.style.display  = "none";
  btn.previousElementSibling.style.display = "inline-flex";
}

document.getElementById("searchInput").addEventListener("input", function() {
  const q = this.value.trim().toLowerCase();
  if (!q) { renderGrid(allLetters); return; }
  const filtered = allLetters.filter(l =>
    l.title.toLowerCase().includes(q) ||
    (l.excerpt || "").toLowerCase().includes(q) ||
    (l.html_content || "").toLowerCase().includes(q)
  );
  renderGrid(filtered);
});

loadData();
</script>
</body>
</html>"""


# ──────────────────────────────────────────────────────────────
# 이메일 발송
# ──────────────────────────────────────────────────────────────

def send_email(subject: str, html_body: str, recipients: list) -> None:
    gmail_user = os.environ["GMAIL_USER"]
    gmail_pass = os.environ["GMAIL_APP_PASS"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"상상인그룹 인재경영실 <{gmail_user}>"
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, recipients, msg.as_string())
    print(f"  ✅ 이메일 발송 완료: {len(recipients)}명")


# ──────────────────────────────────────────────────────────────
# GitHub API — 파일 읽기/쓰기
# ──────────────────────────────────────────────────────────────

def gh_headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

def load_archive(owner: str, repo: str, token: str) -> list:
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{ARCHIVE_FILE}"
    r = requests.get(url, headers=gh_headers(token), timeout=10)
    if r.status_code == 200:
        raw = base64.b64decode(r.json()["content"]).decode("utf-8")
        return json.loads(raw)
    return []

def push_file(content_str: str, path: str, message: str,
              owner: str, repo: str, token: str) -> None:
    url     = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = gh_headers(token)
    r       = requests.get(url, headers=headers, timeout=10)
    sha     = r.json().get("sha") if r.status_code == 200 else None

    payload = {
        "message": message,
        "content": base64.b64encode(content_str.encode("utf-8")).decode(),
    }
    if sha:
        payload["sha"] = sha

    resp   = requests.put(url, headers=headers, json=payload, timeout=20)
    status = "완료" if resp.status_code in (200, 201) else f"실패 ({resp.status_code})"
    print(f"  GitHub [{path}] 업데이트 {status}")


# ──────────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────────

def main():
    now      = datetime.now(KST)
    date_str = now.strftime("%Y년 %m월 %d일")
    date_key = now.strftime("%Y-%m-%d")

    # ── 환경 변수 ──
    notion_token   = os.environ.get("NOTION_TOKEN", "").strip()
    raw_page_id    = os.environ.get("NOTION_PAGE_ID", "").strip()
    gh_owner       = os.environ.get("GITHUB_OWNER", "").strip()
    gh_repo        = os.environ.get("GITHUB_REPO", "").strip()
    gh_token       = os.environ.get("GITHUB_TOKEN", "").strip()
    recipients_str = os.environ.get("EMAIL_RECIPIENTS", "")
    recipients     = [r.strip() for r in recipients_str.split(",") if r.strip()]
    do_send_email  = os.environ.get("SEND_EMAIL", "true").strip().lower() != "false"
    archive_url    = f"https://{gh_owner}.github.io/{gh_repo}/"

    if not notion_token:
        print("❌ NOTION_TOKEN이 설정되지 않았습니다. GitHub Secrets를 확인하세요.")
        return
    if not raw_page_id:
        print("❌ NOTION_PAGE_ID가 비어있습니다. 워크플로우 입력값을 확인하세요.")
        return

    page_id = normalize_page_id(raw_page_id)
    print(f"=== {NEWSLETTER_NAME} 발행 시작 ===")
    print(f"날짜: {date_str}  |  노션 페이지 ID: {page_id}")

    # 1. 아카이브 로드
    print("\n[1] 기존 아카이브 로드 중...")
    archive   = load_archive(gh_owner, gh_repo, gh_token) if gh_token else []
    letter_no = len(archive) + 1
    print(f"  기존 레터 {len(archive)}편  →  이번 호: No.{letter_no}")

    # 2. 노션 페이지 읽기
    print("\n[2] 노션 페이지 읽는 중...")
    try:
        page_data    = fetch_page_meta(page_id, notion_token)
        title        = get_page_title(page_data)
        blocks       = fetch_blocks(page_id, notion_token)
        content_html, excerpt = blocks_to_html(blocks)
        print(f"  제목: {title}  |  블록 {len(blocks)}개")
    except Exception as e:
        print(f"❌ 노션 읽기 실패: {e}")
        raise

    # 3. 이메일 발송
    if do_send_email:
        print("\n[3] 이메일 발송 중...")
        if recipients:
            subject   = f"[인재경영실 Letter] No.{letter_no} — {title}"
            html_body = build_email_html(title, content_html, date_str,
                                         letter_no, archive_url)
            try:
                send_email(subject, html_body, recipients)
            except Exception as e:
                print(f"  ⚠️  이메일 발송 실패: {e}")
        else:
            print("  ⚠️  EMAIL_RECIPIENTS 미설정 — 이메일 발송 건너뜀")
    else:
        print("\n[3] 이메일 발송 건너뜀 (SEND_EMAIL=false)")

    # 4. 아카이브 업데이트 (항상 누적)
    print("\n[4] 아카이브 업데이트 중...")
    new_entry = {
        "id":             str(uuid.uuid4()),
        "number":         letter_no,
        "date":           date_key,
        "title":          title,
        "excerpt":        excerpt,
        "notion_page_id": raw_page_id,
        "html_content":   content_html,
    }
    archive.insert(0, new_entry)  # 최신순 prepend

    push_file(
        json.dumps(archive, ensure_ascii=False, indent=2),
        ARCHIVE_FILE,
        f"letter: No.{letter_no} — {title}",
        gh_owner, gh_repo, gh_token,
    )

    # 5. GitHub Pages 업데이트
    print("\n[5] GitHub Pages 업데이트 중...")
    push_file(
        build_index_html(),
        INDEX_FILE,
        f"pages: update archive for letter No.{letter_no}",
        gh_owner, gh_repo, gh_token,
    )

    print(f"\n✅ 완료! No.{letter_no} '{title}' 발행 완료")
    print(f"   아카이브 URL: {archive_url}")


if __name__ == "__main__":
    main()
