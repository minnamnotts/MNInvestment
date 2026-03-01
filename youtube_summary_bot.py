import os
import shutil
import tempfile
import time
import json
from datetime import datetime

import anthropic
import requests
from dotenv import load_dotenv

# --- [설정 영역] ---
_script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_script_dir, ".env"))
load_dotenv(os.path.expanduser("~/.env"))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
YOUTUBE_API_KEY   = os.environ.get("YOUTUBE_API_KEY")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHANNEL_ID")

if not all([ANTHROPIC_API_KEY, YOUTUBE_API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
    missing = [k for k, v in {
        "ANTHROPIC_API_KEY":  ANTHROPIC_API_KEY,
        "YOUTUBE_API_KEY":    YOUTUBE_API_KEY,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    }.items() if not v]
    print(f"❌ 누락된 환경변수: {', '.join(missing)}")
    exit(1)

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# 이미 처리한 영상 ID 저장 (중복 발송 방지)
_PROCESSED_IDS_FILE = os.path.join(_script_dir, "youtube_summary_processed.json")
_MAX_PROCESSED_IDS = 500  # 최대 보관 개수 (오래된 것부터 삭제)


def _load_processed_ids() -> set:
    try:
        with open(_PROCESSED_IDS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_processed_id(video_id: str, current: set) -> None:
    ids = list(current | {video_id})
    if len(ids) > _MAX_PROCESSED_IDS:
        ids = ids[-_MAX_PROCESSED_IDS:]
    with open(_PROCESSED_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False, indent=0)


# ────────────────────────────────────────────────
# 채널 설정 (최신 영상 기준, keyword 있으면 채널 내 검색)
# ────────────────────────────────────────────────
CHANNELS = {
    "증시각도기TV": {
        "id": "UCdOjVxkj5JA0iDu3_xcsTyQ",
    },
    "삼프로TV": {
        "id": "UChlv4GSd7OQl3js-jkLOnFA",
    },
    "한경_빈난새개장전": {
        "id": "UCWskYkV4c4S9D__rsfOl2JA",
        "keyword": "빈난새의 개장전 요것만",
    },
}


# ────────────────────────────────────────────────
# 1) 영상 가져오기
# ────────────────────────────────────────────────
def get_latest_video(channel_name: str, channel_id: str, keyword: str = None) -> dict | None:
    try:
        url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            "key":        YOUTUBE_API_KEY,
            "channelId":  channel_id,
            "part":       "snippet",
            "order":      "date",
            "maxResults": 1,
            "type":       "video",
        }
        if keyword:
            params["q"] = keyword

        r    = requests.get(url, params=params, timeout=10)
        data = r.json()

        if "error" in data:
            print(f"    ❌ YouTube API 오류: {data['error'].get('message', '')}")
            return None

        items = data.get("items", [])
        if not items:
            print(f"    ⚠️  {channel_name}: 영상 없음 (키워드: {keyword})")
            return None

        item     = items[0]
        video_id = item["id"]["videoId"]
        snippet  = item["snippet"]

        return {
            "video_id":    video_id,
            "title":       snippet.get("title", ""),
            "description": snippet.get("description", "")[:300],
            "published":   snippet.get("publishedAt", "")[:10],
            "url":         f"https://www.youtube.com/watch?v={video_id}",
            "channel":     channel_name,
            "keyword":     keyword or "최신",
        }
    except Exception as e:
        print(f"    ❌ YouTube API 오류 ({channel_name}): {e}")
        return None


# ────────────────────────────────────────────────
# 2) 자막 추출 (유튜브 자막 → 없으면 Whisper 자동생성)
# ────────────────────────────────────────────────
def _get_youtube_transcript(video_id: str) -> str | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import NoTranscriptFound

        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["ko"])
        except NoTranscriptFound:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            transcript = transcript_list.find_generated_transcript(["ko", "en"]).fetch()
        full_text = " ".join([t["text"] for t in transcript])
        return full_text[:8000]
    except Exception:
        return None


def _get_ffmpeg_path() -> str | None:
    for base in ("/opt/homebrew/bin", "/usr/local/bin"):
        ffmpeg = os.path.join(base, "ffmpeg")
        if os.path.isfile(ffmpeg) or os.path.isfile(ffmpeg + ".exe"):
            return base
    return None


def _get_js_runtime_path() -> dict | None:
    for runtime, exe in (("node", "node"), ("deno", "deno")):
        path = shutil.which(exe)
        if not path:
            for base in ("/opt/homebrew/bin", "/usr/local/bin"):
                candidate = os.path.join(base, exe)
                if os.path.isfile(candidate):
                    path = candidate
                    break
        if path:
            return {runtime: {"path": path}}
    return None


def _generate_transcript_with_whisper(video_id: str) -> str | None:
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        import yt_dlp

        ffmpeg_dir = _get_ffmpeg_path()
        if not ffmpeg_dir:
            return None
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

        js_runtime = _get_js_runtime_path()
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "audio.%(ext)s")
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": out_path,
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
                "quiet": True,
                "ffmpeg_location": ffmpeg_dir,
            }
            if js_runtime:
                ydl_opts["js_runtimes"] = js_runtime

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            m4a_path = os.path.join(tmpdir, "audio.m4a")
            if not os.path.exists(m4a_path):
                return None

            import whisper
            model = whisper.load_model("base")
            result = model.transcribe(m4a_path, language="ko", fp16=False)
            text = (result.get("text") or "").strip()[:8000]
            return text if text else None
    except Exception:
        return None


def get_transcript(video_id: str) -> str | None:
    text = _get_youtube_transcript(video_id)
    if text:
        return text
    print(f"    📝 자막 없음 → Whisper 자동 생성 시도...")
    return _generate_transcript_with_whisper(video_id)


# ────────────────────────────────────────────────
# 3) Claude 요약
# ────────────────────────────────────────────────
def summarize_with_claude(video_info: dict, transcript: str) -> dict:
    content        = transcript if transcript else f"제목: {video_info['title']}\n설명: {video_info['description']}"
    has_transcript = transcript is not None

    prompt = f"""다음은 유튜브 채널 [{video_info['channel']}]의 영상입니다.

제목: {video_info['title']}
날짜: {video_info['published']}
URL: {video_info['url']}

{"자막 내용:" if has_transcript else "영상 설명 (자막 없음):"}
{content}

투자자 관점에서 아래 JSON 형식으로만 요약하세요.
JSON 외 다른 텍스트 없이 순수 JSON만 출력하세요.

{{
  "key_topics": ["핵심 주제 1", "핵심 주제 2", "핵심 주제 3"],
  "market_view": "시장 전반 전망 요약 (2-3문장)",
  "stock_mentions": [
    {{
      "name": "종목명",
      "view": "긍정/중립/부정",
      "reason": "이유 한 줄"
    }}
  ],
  "macro_points": "매크로 관련 내용 (금리, 환율, 지표 등) (2문장 이내)",
  "action_items": ["투자자 주목 포인트 1", "포인트 2"],
  "overall_sentiment": "긍정/중립/부정",
  "one_line_summary": "영상 전체 한 줄 요약"
}}"""

    try:
        message = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        text   = message.content[0].text.strip()
        text   = text.replace("```json", "").replace("```", "").strip()
        result = json.loads(text)
        result["has_transcript"] = has_transcript
        return result
    except Exception as e:
        print(f"    ❌ Claude 요약 오류: {e}")
        return {
            "one_line_summary":  "요약 실패",
            "market_view":       "",
            "key_topics":        [],
            "stock_mentions":    [],
            "macro_points":      "",
            "action_items":      [],
            "overall_sentiment": "중립",
            "has_transcript":    False,
        }


# ────────────────────────────────────────────────
# 4) 텔레그램 발송
# ────────────────────────────────────────────────
SENTIMENT_EMOJI = {"긍정": "🟢", "중립": "🟡", "부정": "🔴"}


def format_youtube_message(video_info: dict, summary: dict) -> str:
    sentiment = SENTIMENT_EMOJI.get(summary.get("overall_sentiment", "중립"), "🟡")

    lines = [
        f"🎬 *{video_info['channel']}* | 🔍 {video_info['keyword']}",
        f"📹 {video_info['title']}",
        f"📅 {video_info['published']} | {sentiment} {summary.get('overall_sentiment', '')}",
        f"🔗 {video_info['url']}",
        "─────────────────────",
        "💬 *한줄 요약*",
        summary.get("one_line_summary", ""),
    ]

    topics = summary.get("key_topics", [])
    if topics:
        lines += [f"\n🏷 *핵심 주제*", " | ".join(topics)]

    if summary.get("market_view"):
        lines += [f"\n📊 *시장 전망*", summary["market_view"]]

    stocks = summary.get("stock_mentions", [])
    if stocks:
        lines.append(f"\n📌 *언급 종목*")
        for s in stocks[:5]:
            e = SENTIMENT_EMOJI.get(s.get("view", "중립"), "🟡")
            lines.append(f"{e} {s.get('name', '')} — {s.get('reason', '')}")

    if summary.get("macro_points"):
        lines += [f"\n🌍 *매크로*", summary["macro_points"]]

    actions = summary.get("action_items", [])
    if actions:
        lines.append(f"\n⚡ *주목 포인트*")
        for a in actions:
            lines.append(f"• {a}")

    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    try:
        url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"    ✈️  텔레그램 발송 완료")
            return True
        else:
            print(f"    ❌ 텔레그램 오류: {r.text[:100]}")
            return False
    except Exception as e:
        print(f"    ❌ 텔레그램 오류: {e}")
        return False


# ────────────────────────────────────────────────
# 메인 실행
# ────────────────────────────────────────────────
def run_youtube_summary():
    processed_ids = _load_processed_ids()

    for channel_name, config in CHANNELS.items():
        print(f"\n{'='*60}")
        print(f"📺 {channel_name} | 키워드: {config.get('keyword', '최신')}")
        print("=" * 60)

        video_info = get_latest_video(channel_name, config["id"], config.get("keyword"))
        if not video_info:
            continue

        if video_info["video_id"] in processed_ids:
            print(f"  ⏭ 이미 처리됨 (건너뜀): {video_info['title'][:40]}...")
            continue

        print(f"  ✓ 영상: {video_info['title'][:50]}...")
        print(f"  📝 자막 추출 중...")
        transcript = get_transcript(video_info["video_id"])

        if not transcript:
            print(f"  ⏭ 자막 추출 불가 (자막 제공 안 하는 영상)")
            msg = (
                f"🎬 *{video_info['channel']}* | 🔍 {video_info['keyword']}\n"
                f"📹 {video_info['title']}\n"
                f"📅 {video_info['published']}\n"
                f"🔗 {video_info['url']}\n"
                "─────────────────────\n"
                "⚠️ *자막 제공 안 하는 영상입니다*"
            )
            print(msg)
        else:
            print(f"  ✓ 자막 {len(transcript)}자 추출 완료")
            print(f"  🤖 Claude 요약 중...")
            summary = summarize_with_claude(video_info, transcript)
            msg = format_youtube_message(video_info, summary)
            print(msg)

        if send_telegram(msg):
            processed_ids.add(video_info["video_id"])
            _save_processed_id(video_info["video_id"], processed_ids)
        time.sleep(3)


if __name__ == "__main__":
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        print("📦 youtube-transcript-api 설치 중...")
        os.system("pip3 install youtube-transcript-api")

    run_youtube_summary()
