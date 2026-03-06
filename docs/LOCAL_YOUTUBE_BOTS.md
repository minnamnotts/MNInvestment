# 유튜브 봇 로컬 실행 가이드

## 1. 수동 실행

```bash
# 둘 다 한 번에 실행
./run_youtube_bots.sh

# 각각 실행
python3 youtube_summary_bot.py    # MN Investment 채널용
python3 "YouTube for Jiha.py"     # MNI for Jiha 개인용
```

## 2. 자동 실행 (launchd, macOS)

**스케줄 (로컬 시간, SGT 기준)**  
- 아침: Jiha 08:00, 서머리 08:40  
- 저녁 5시: Jiha 17:00, 서머리 17:00  
- 밤 11시: Jiha 23:00, 서머리 23:00  

**처리 이력 (중복 방지)**  
- 서머리봇: `youtube_summary_processed.json`  
- 지하봇: `youtube_jiha_processed.json` (서로 분리됨)

```bash
cd "/Users/minsmac/MNI_Finance project"

# 아침 + 저녁 + 11시 총 6개 job 등록
ln -sf "$(pwd)/com.mni.youtube-jiha.plist" ~/Library/LaunchAgents/
ln -sf "$(pwd)/com.mni.youtube-summary.plist" ~/Library/LaunchAgents/
ln -sf "$(pwd)/com.mni.youtube-jiha-pm.plist" ~/Library/LaunchAgents/
ln -sf "$(pwd)/com.mni.youtube-summary-pm.plist" ~/Library/LaunchAgents/
ln -sf "$(pwd)/com.mni.youtube-jiha-11pm.plist" ~/Library/LaunchAgents/
ln -sf "$(pwd)/com.mni.youtube-summary-11pm.plist" ~/Library/LaunchAgents/

launchctl load ~/Library/LaunchAgents/com.mni.youtube-jiha.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-summary.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-jiha-pm.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-summary-pm.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-jiha-11pm.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-summary-11pm.plist
```

### 로그 확인

- 서머리 (아침): `youtube_summary.log`, `youtube_summary_err.log`
- 서머리 (저녁): `youtube_summary_pm.log`, `youtube_summary_pm_err.log`
- 서머리 (11시): `youtube_summary_11pm.log`, `youtube_summary_11pm_err.log`
- Jiha (아침): `youtube_jiha.log`, `youtube_jiha_err.log`
- Jiha (저녁): `youtube_jiha_pm.log`, `youtube_jiha_pm_err.log`
- Jiha (11시): `youtube_jiha_11pm.log`, `youtube_jiha_11pm_err.log`

### 실행 중지/재시작

```bash
launchctl unload ~/Library/LaunchAgents/com.mni.youtube-summary.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-summary.plist

launchctl unload ~/Library/LaunchAgents/com.mni.youtube-jiha.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-jiha.plist

# 저녁 job
launchctl unload ~/Library/LaunchAgents/com.mni.youtube-summary-pm.plist
launchctl unload ~/Library/LaunchAgents/com.mni.youtube-jiha-pm.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-summary-pm.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-jiha-pm.plist
```
