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
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID")

if not all([ANTHROPIC_API_KEY, YOUTUBE_API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
    missing = [k for k, v in {
        "ANTHROPIC_API_KEY":  ANTHROPIC_API_KEY,
        "YOUTUBE_API_KEY":    YOUTUBE_API_KEY,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID":   TELEGRAM_CHAT_ID,
    }.items() if not v]
    print(f"❌ 누락된 환경변수: {', '.join(missing)}")
    exit(1)

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# 이미 처리한 영상 ID 저장 (중복 발송 방지)
_PROCESSED_IDS_FILE = os.path.join(_script_dir, "youtube_summary_processed.json")
_MAX_PROCESSED_IDS = 500  # 최대 보관 개수 (오래된 것부터 삭제)


def _load_processed_ids() -> set[str]:
    try:
        with open(_PROCESSED_IDS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_processed_id(video_id: str, current: set[str]) -> None:
    ids = list(current | {video_id})
    if len(ids) > _MAX_PROCESSED_IDS:
        ids = ids[-_MAX_PROCESSED_IDS:]
    with open(_PROCESSED_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False, indent=0)


# ────────────────────────────────────────────────
# 채널 설정 (최신 영상 기준)
# ────────────────────────────────────────────────
CHANNELS = {
    "소수몽키": {
        "id": "UCC3yfxS5qC6PCwDzetUuEWg",
    },
    "서재형의 투자교실": {
        "id": "UCtmKBFeri9hx9DOaVSSvvvw",
    },
    "경제사냥꾼": {
        "id": "UC7usMJDHmtbs_oegmzQKKMA",
    },
    "올랜도킴": {
        "id": "UCwSSqi-s0wcH6pJbH3YPZqQ",
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
def get_transcript(video_id: str) -> str | None:
    # 1) 유튜브 자막 시도
    text = _get_youtube_transcript(video_id)
    if text:
        return text

    # 2) 자막 없으면 Whisper로 자동생성
    print(f"    📝 자막 없음 → Whisper로 자동 생성 시도...")
    return _generate_transcript_with_whisper(video_id)


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
    """Homebrew 등에 설치된 ffmpeg 디렉터리 경로 (PATH용)"""
    for base in ("/opt/homebrew/bin", "/usr/local/bin"):
        ffmpeg = os.path.join(base, "ffmpeg")
        if os.path.isfile(ffmpeg) or os.path.isfile(ffmpeg + ".exe"):
            return base
    return None


def _get_js_runtime_path() -> dict | None:
    """Node.js 또는 Deno 경로 탐색 (yt-dlp EJS용)"""
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
    """yt-dlp로 오디오 다운로드 후 Whisper로 변환"""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        import yt_dlp

        ffmpeg_dir = _get_ffmpeg_path()
        if not ffmpeg_dir:
            print(f"    ⚠️  ffmpeg 미발견. brew install ffmpeg 실행 후 재시도")
            return None

        # Whisper/yt-dlp subprocess가 ffmpeg를 찾을 수 있도록 PATH에 추가
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
    except ImportError as e:
        print(f"    ⚠️  yt-dlp 또는 whisper 미설치: pip install yt-dlp openai-whisper")
        return None
    except Exception as e:
        print(f"    ❌ Whisper 자동생성 실패: {e}")
        return None


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

[지시사항]
- 영상에 나온 구체적 내용을 담되, 내용이 없는 항목은 "" 또는 []로 두고 생략하세요. 굳이 채울 필요 없음.
- 숫자, 종목명, 시점, 조건 등 영상에서 언급된 구체 정보를 포함하세요.
- 언급 종목의 view는 "긍정" | "중립" | "부정" 중 하나로 판단.

아래 JSON 형식으로만 출력하세요. JSON 외 다른 텍스트 없이 순수 JSON만 출력하세요.

{{
  "one_line_summary": "한줄 요약 (핵심 결론)",
  "key_topics": ["핵심 주제 1", "핵심 주제 2"],
  "market_view": "시장 전망 / 투자 관련 유의사항",
  "stock_mentions": [
    {{"name": "종목명", "view": "긍정|중립|부정", "reason": "언급 내용 요약"}}
  ],
  "macro_points": "매크로 (금리, 환율, 지표, 사모대출 등)",
  "action_items": ["주목 포인트 1", "주목 포인트 2"]
}}"""

    try:
        message = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
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
            "one_line_summary": "요약 실패",
            "has_transcript": False,
        }


# ────────────────────────────────────────────────
# 4) 텔레그램 발송
# ────────────────────────────────────────────────
SENTIMENT_EMOJI = {"긍정": "🟢", "중립": "🟡", "부정": "🔴"}


def format_youtube_message(video_info: dict, summary: dict) -> str:
    lines = [
        f"🎬 *{video_info['channel']}* | 🔍 {video_info['keyword']}",
        f"📹 {video_info['title']}",
        f"📅 {video_info['published']}",
        f"🔗 {video_info['url']}",
        "─────────────────────",
    ]

    if summary.get("one_line_summary"):
        lines += ["💬 *한줄 요약*", summary["one_line_summary"]]
    if summary.get("key_topics"):
        lines += ["\n🏷 *핵심 주제*", " | ".join(summary["key_topics"])]
    if summary.get("market_view"):
        lines += ["\n📊 *시장 전망*", summary["market_view"]]
    if summary.get("stock_mentions"):
        lines.append("\n📌 *언급 종목*")
        for s in summary["stock_mentions"][:10]:
            e = SENTIMENT_EMOJI.get(s.get("view", "중립"), "🟡")
            lines.append(f"{e} {s.get('name', '')} — {s.get('reason', '')}")
    if summary.get("macro_points"):
        lines += ["\n🌍 *매크로*", summary["macro_points"]]
    if summary.get("action_items"):
        lines.append("\n⚡ *주목 포인트*")
        for a in summary["action_items"]:
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
    today = datetime.now()
    now = today.strftime("%Y-%m-%d %H:%M")
    print(f"🎬 유튜브 서머리 시작 ({now})")

    send_telegram(f"🎬 *유튜브 브리핑 시작*\n채널: {', '.join(CHANNELS.keys())}\n🕐 {now}")
    time.sleep(1)

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

        if transcript:
            print(f"  ✓ 자막 {len(transcript)}자 추출 완료")

        print(f"  🤖 Claude 요약 중...")
        summary = summarize_with_claude(video_info, transcript)

        msg = format_youtube_message(video_info, summary)
        print(msg)
        if send_telegram(msg):
            processed_ids.add(video_info["video_id"])
            _save_processed_id(video_info["video_id"], processed_ids)
        time.sleep(3)

    send_telegram(f"✅ *유튜브 브리핑 완료*\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("\n✅ 유튜브 서머리 완료")


if __name__ == "__main__":
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        print("📦 youtube-transcript-api 설치 중...")
        os.system("pip3 install youtube-transcript-api")

    run_youtube_summary()
