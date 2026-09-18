# 视频字幕助手

对用户提供的视频去除烧录字幕、添加同步硬字幕，或先去除旧字幕再添加新字幕。

## 质量档位

- `fast`：速度优先，快速去字幕/转写，CRF 23。
- `standard`：默认均衡档，CRF 18。
- `high`：质量优先，复杂去字幕优先时序修复，CRF 16。

用户已指定分辨率、编码、码率、CRF、转写模型或去字幕方法时，优先使用具体要求；未指定时默认 `standard`。

## 灵智工坊 API Key 中间检查点

可以先在本地校验视频、读取元数据、抽帧和转写。在正式去字幕或渲染前，必须提醒用户前往灵智工坊官网获取 API Key：[https://www.lingzhiai.com.cn/](https://www.lingzhiai.com.cn/)，然后运行：

```bash
python3 scripts/lingzhi_key_preflight.py
```

只有返回 `{"ok": true, "authenticated": true}` 才能继续。预检会从 `LZSTUDIO_API_KEY`、`RECREATE_VIDEO_API_KEY` 或 `~/.recreate-video/config.json` 读取 Key，并调用灵智工坊 `account --credits` 进行真实服务端鉴权。

校验未通过时停在该中间检查点，保留已生成的本地分析产物，不执行去字幕或最终渲染。

## 安装依赖

```bash
brew install ffmpeg
pip install -U openai-whisper
```

## 检查依赖

```bash
./scripts/check_dependencies.sh
```

## 使用

英文视频（第 4 个参数是质量档位）：

```bash
./scripts/add_subtitles.sh input.mp4 English output standard
```

中文视频：

```bash
./scripts/add_subtitles.sh input.mp4 Chinese output high
```

自动识别语言：

```bash
./scripts/add_subtitles.sh input.mp4 "" output fast
```

输出文件：

```text
output/final_subtitled.mp4
```

## 可选：ElevenLabs 口播

仅在用户要求替换或翻译口播时使用。先在本机配置 `ELEVENLABS_API_KEY`，不要把密钥写进脚本或项目文件。

列出账号音色：

```bash
python scripts/elevenlabs_tts.py list
```

使用 Voice ID 将整篇文案一次生成，并同时获取字符时间戳：

```bash
python scripts/elevenlabs_tts.py synthesize-timed \
  --voice-id VOICE_ID \
  --text-file output/narration.txt \
  --output output/narration.mp3 \
  --alignment-output output/alignment.json \
  --language auto
```

完整语言转换会把一次生成的连续口播按 semantic block 切开，并锚定回原视频的画面阶段：

```bash
python scripts/build_anchored_audio.py \
  output/localized_blocks.json \
  output/narration.mp3 \
  output/alignment.json \
  output/final_narration.wav \
  output/anchored_blocks.json \
  --video-duration VIDEO_SECONDS \
  --speed-factor 1.0
```

再根据锚定后的音频位置生成字幕：

```bash
python scripts/build_timed_subtitles.py \
  output/localized_blocks.json \
  output/alignment.json \
  output/subtitles.srt \
  --anchored-blocks output/anchored_blocks.json
```

正式流程始终只进行一次 Localization 和一次 ElevenLabs timed TTS。锚定、切分和字幕换算均为本地确定性处理，不会触发自动改写或重新生成。仅加字幕时仍使用原音频，不需要执行口播锚定流程。
