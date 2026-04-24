# YouTube Bots Local Run Guide

## 1. 수동 실행

```bash
# 둘 다 한 번에 실행
./run_youtube_bots.sh

# 각각 실행
python3 youtube_summary_bot.py    # MN Investment 채널용
python3 "YouTube for VIP.py"     # MNI for VIP 개인용
```

## 2. 자동 실행 (launchd, macOS)

**스케줄 (로컬 시간, SGT 기준)**  
- 아침: VIP 08:00, 서머리 08:40  
- 저녁 5시: VIP 17:00, 서머리 17:00  
- 밤 11시: VIP 23:00, 서머리 23:00  

**처리 이력 (중복 방지)**  
- 서머리봇: `youtube_summary_processed.json`  
- 지하봇: `youtube_vip_processed.json` (서로 분리됨)

```bash
cd "/Users/minsmac/MNI_Finance project"

# 아침 + 저녁 + 11시 총 6개 job 등록
ln -sf "$(pwd)/com.mni.youtube-vip.plist" ~/Library/LaunchAgents/
ln -sf "$(pwd)/com.mni.youtube-summary.plist" ~/Library/LaunchAgents/
ln -sf "$(pwd)/com.mni.youtube-vip-pm.plist" ~/Library/LaunchAgents/
ln -sf "$(pwd)/com.mni.youtube-summary-pm.plist" ~/Library/LaunchAgents/
ln -sf "$(pwd)/com.mni.youtube-vip-11pm.plist" ~/Library/LaunchAgents/
ln -sf "$(pwd)/com.mni.youtube-summary-11pm.plist" ~/Library/LaunchAgents/

launchctl load ~/Library/LaunchAgents/com.mni.youtube-vip.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-summary.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-vip-pm.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-summary-pm.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-vip-11pm.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-summary-11pm.plist
```

### 로그 확인

- 서머리 (아침): `youtube_summary.log`, `youtube_summary_err.log`
- 서머리 (저녁): `youtube_summary_pm.log`, `youtube_summary_pm_err.log`
- 서머리 (11시): `youtube_summary_11pm.log`, `youtube_summary_11pm_err.log`
- VIP (아침): `youtube_vip.log`, `youtube_vip_err.log`
- VIP (저녁): `youtube_vip_pm.log`, `youtube_vip_pm_err.log`
- VIP (11시): `youtube_vip_11pm.log`, `youtube_vip_11pm_err.log`

### 실행 중지/재시작

```bash
launchctl unload ~/Library/LaunchAgents/com.mni.youtube-summary.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-summary.plist

launchctl unload ~/Library/LaunchAgents/com.mni.youtube-vip.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-vip.plist

# 저녁 job
launchctl unload ~/Library/LaunchAgents/com.mni.youtube-summary-pm.plist
launchctl unload ~/Library/LaunchAgents/com.mni.youtube-vip-pm.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-summary-pm.plist
launchctl load ~/Library/LaunchAgents/com.mni.youtube-vip-pm.plist
```
