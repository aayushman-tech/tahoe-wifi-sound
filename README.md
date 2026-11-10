#  Mac Live Audio Streamer

Live, ultra-low latency system audio streamer for **macOS 26 (Tahoe)** and Hackintosh systems. Streams all audio from **BlackHole 2ch** to any browser (iPad, iPhone, Android, laptop) over your local Wi-Fi / LAN with **no apps required on the receiver**.

---

## ⚡ Quick Start

### 1. Install Dependencies
You only need Python 3 and four lightweight pip packages. **No Homebrew packages required** for standard low-latency streaming:

```bash
pip3 install sounddevice numpy aiohttp qrcode
```

*(Optional: If you want the background HTTP MP3 stream fallback for lock-screen listening on iOS Safari, install FFmpeg: `brew install ffmpeg`)*

### 2. Set macOS Sound Output
1. Open **System Settings > Sound**.
2. Under **Output**, select **BlackHole 2ch**.
   *(All system audio from YouTube, music, games, and apps will now route into BlackHole).*

### 3. Start the Streamer
Run the following single command from this directory:

```bash
python3 server.py
```

The server will automatically:
- Detect your **BlackHole 2ch** device.
- Discover your Mac's real LAN IP (e.g. `http://192.168.31.175:8000`).
- Print an **ASCII QR Code** in your terminal.
- Launch a live **ASCII VU level meter** showing volume in dBFS and connected clients.

---

## 📱 Listening on iPad / iPhone / Receiver

1. **Open the Stream**:
   - Point your iPad/iPhone camera at the terminal QR code and tap the link, **OR**
   - Open Safari/Chrome and navigate to `http://192.168.x.x:8000`.
2. **Tap "Tap to Listen"**:
   - iOS Safari requires a direct user gesture to unlock the Web Audio engine. Once tapped, you will immediately hear live audio.
3. **Adjust Volume**:
   - Control receiver volume using your device's physical buttons or the on-screen volume slider (boosts up to 150%).

---

## 🎧 Streaming Modes

| Mode | Technology | Latency | iOS Lock-Screen | Best For |
|---|---|---|---|---|
| **Ultra-Low Latency (Default)** | 48kHz Stereo 16-bit PCM over WebSocket + Web Audio API | **< 150 ms** | Pauses when tab minimized / screen locked | Watching videos, YouTube, gaming, real-time feedback |
| **HTTP Fallback** | Chunked MP3 / WAV stream via `<audio>` tag (`/stream.mp3`) | **2 – 4 s** | ✅ Continues playing in background & lock screen | Casual music listening, podcasts, walking around |

---

## 🔒 Security & LAN Access Control

By default, the server listens on your local network without authentication. To restrict access to authorized family/devices:

```bash
python3 server.py --password MySecretPassword
```

- The printed terminal link and QR code will automatically include `?token=MySecretPassword`, so scanning the QR code still connects in 1 tap.
- Any unauthorized visitor will be prompted for the password before audio can be received.

---

## 🛠️ Command-Line Flags

```text
usage: server.py [-h] [--host HOST] [--port PORT] [--device DEVICE]
                 [--list-devices] [--samplerate SAMPLERATE]
                 [--channels CHANNELS] [--buffer-ms BUFFER_MS]
                 [--password TOKEN] [--fallback] [--no-qr]

optional arguments:
  -h, --help            Show this help message and exit
  --host HOST           Binding IP address (default: 0.0.0.0)
  --port PORT           Port to run web server on (default: 8000)
  --device DEVICE       Specify audio input device ID or name (default: auto-detect BlackHole)
  --list-devices        List all audio input/output devices and exit
  --samplerate RATE     Sample rate in Hz (default: 48000)
  --channels CHANNELS   Audio channels (default: 2 stereo)
  --buffer-ms MS        Audio packet duration in ms (default: 20ms)
  --password TOKEN      Require secret password / access token
  --fallback            Keep HTTP MP3 transcoder running for fallback mode
  --no-qr               Do not display the ASCII QR code
```

---

## 🍏 macOS 26 Tahoe & Hackintosh Specifics

### 1. Microphone Permission
macOS categorizes all virtual audio drivers (including BlackHole) under the **Microphone (TCC)** subsystem.
- When you first run `python3 server.py` in Terminal or iTerm, macOS will ask:
  > *"Terminal" would like to access the microphone.*
- Click **Allow**.
- If denied or audio is silent, go to:
  **System Settings > Privacy & Security > Microphone** and toggle **ON** your terminal app (Terminal, iTerm, Alacritty, VS Code).

### 2. macOS Firewall Prompt
If the macOS Application Firewall is active, you may see:
> *"Do you want the application "python3" to accept incoming network connections?"*
- Click **Allow**.
- If missed or blocked, open **System Settings > Network > Firewall > Options** and ensure incoming connections for Python are allowed.

### 3. BlackHole Volume & Digital Clipping
- Virtual loopback drivers like BlackHole **have no hardware volume attenuation**. macOS will gray out or lock the system volume bar at 100% (unity gain / 0 dBFS).
- If any application (e.g. VLC with preamp > 100%, EQ boost, or YouTube) boosts audio above 0 dBFS, **digital clipping (harsh distortion)** will occur.
- **Rule of thumb**: Keep the playback volume in YouTube/Spotify at ~90-100%, and adjust your actual listening volume on your iPad / phone using the web slider or physical volume buttons.
- The server terminal and web page will display `[CLIP!]` warning if clipping occurs.

### 4. Audio Verification
To verify that sound is flowing:
1. Play any video on YouTube in your Mac browser.
2. Observe the terminal VU meter:
   ```text
   [Audio VU] [████████████░░░░░░░░] -16.4 dBFS | Clients: 1
   ```
   If the bar is moving, audio is reaching BlackHole and streaming live!
3. On your iPad, the on-screen canvas visualizer will show an animated waveform.

---

## 🩺 Troubleshooting

### 1. iPad won't connect / page won't load
- **Same Wi-Fi network**: Verify your iPad and Mac are connected to the exact same Wi-Fi SSID / local subnet. (Check that the Mac is not on a guest network or VPN with client isolation).
- **macOS Firewall**: Check **System Settings > Network > Firewall** and temporarily disable it or allow python3.
- **Port Conflict**: If port 8000 is used by another service, start with `--port 8080`.

### 2. No sound after tapping "Tap to Listen"
- **Check macOS Output**: Ensure **BlackHole 2ch** is selected in **System Settings > Sound > Output**.
- **Microphone Permission**: Ensure your terminal app has Microphone permission in **System Settings > Privacy & Security > Microphone**.
- **Mute switch**: Ensure your iPad physical mute switch (or Control Center Silent Mode) is turned OFF, and iPad volume is turned up.

### 3. Crackling or popping audio
- **Wi-Fi interference**: If you are far from your router, 2.4GHz interference can cause packet drops. Connect to 5GHz Wi-Fi.
- **CPU overload**: Check Activity Monitor. On a 4-core i3, `server.py` uses < 0.5% CPU. If other apps are hogging 100% CPU, increase `--buffer-ms 30` or `--buffer-ms 40`.

### 4. Latency higher than expected
- In the web interface, ensure you are on the **⚡ Ultra-Low Latency (WS)** tab. The fallback `<audio>` tab adds 2-3 seconds of native browser buffer.
- The WebSocket player automatically resynchronizes if packet drift exceeds 250ms.
