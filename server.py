#!/usr/bin/env python3
"""
Mac Audio Streamer
Streams live system audio from macOS (BlackHole 2ch) to any browser over LAN.
Ultra-low latency (< 150ms) using uncompressed PCM over WebSocket + Web Audio API.
"""

import argparse
import asyncio
import math
import os
import shutil
import socket
import subprocess
import sys
import time

try:
    import numpy as np
    import sounddevice as sd
    from aiohttp import web, WSMsgType
    import qrcode
except ImportError as e:
    print(f"\n❌ Missing dependency: {e}")
    print("Please install required packages using:")
    print("  pip3 install sounddevice numpy aiohttp qrcode\n")
    sys.exit(1)

# Global State
connected_ws_clients = {}     # client_id -> asyncio.Queue
connected_http_clients = {}   # client_id -> asyncio.Queue
meter_state = {
    'rms': 0.0,
    'peak': 0.0,
    'clipped': False,
    'last_update': 0.0
}
ffmpeg_proc = None
ffmpeg_stdin = None

# ---------------------------------------------------------------------------
# Network & IP Utilities
# ---------------------------------------------------------------------------
def get_lan_ip():
    """Discover the primary LAN IP address of this Mac."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to a non-routable address to force routing table resolution
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def print_qr_code(url):
    """Print a high-contrast ASCII QR code in the terminal."""
    try:
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        print("\n\033[1;36mScan QR Code with iPhone / iPad Camera to Open:\033[0m")
        qr.print_ascii(invert=True)
    except Exception as e:
        print(f"Could not render terminal QR code: {e}")

# ---------------------------------------------------------------------------
# Audio Device Discovery
# ---------------------------------------------------------------------------
def list_audio_devices():
    """Print all available audio devices and exit."""
    devices = sd.query_devices()
    default_in = sd.default.device[0]
    default_out = sd.default.device[1]

    print("\n" + "=" * 76)
    print("AVAILABLE macOS AUDIO DEVICES")
    print("=" * 76)
    print(f"{'ID':<4} {'Device Name':<38} {'In/Out':<10} {'Sample Rate':<12} {'Notes'}")
    print("-" * 76)

    for i, dev in enumerate(devices):
        name = dev['name'][:36]
        chans = f"{dev['max_input_channels']} in / {dev['max_output_channels']} out"
        sr = f"{int(dev['default_samplerate'])} Hz"
        notes = []
        if i == default_in:
            notes.append("Default Input")
        if i == default_out:
            notes.append("Default Output")
        if "blackhole" in dev['name'].lower():
            notes.append("★ BlackHole")

        print(f"[{i:<2}] {name:<38} {chans:<10} {sr:<12} {', '.join(notes)}")
    print("=" * 76 + "\n")

def find_blackhole_device(user_device=None):
    """Auto-detect or validate the specified input audio device."""
    devices = sd.query_devices()

    # User specified an exact device index or name
    if user_device is not None:
        try:
            dev_idx = int(user_device)
            dev_info = sd.query_devices(dev_idx, 'input')
            return dev_idx, dev_info['name']
        except (ValueError, sd.PortAudioError):
            for i, dev in enumerate(devices):
                if user_device.lower() in dev['name'].lower() and dev['max_input_channels'] > 0:
                    return i, dev['name']
            print(f"❌ Error: Specified audio device '{user_device}' not found.")
            sys.exit(1)

    # Auto-detection: First search for "BlackHole 2ch"
    for i, dev in enumerate(devices):
        if 'blackhole 2ch' in dev['name'].lower() and dev['max_input_channels'] > 0:
            return i, dev['name']

    # Secondary: search for any BlackHole variant (16ch, 64ch)
    for i, dev in enumerate(devices):
        if 'blackhole' in dev['name'].lower() and dev['max_input_channels'] > 0:
            return i, dev['name']

    # Fallback check: default input device
    default_idx = sd.default.device[0]
    if default_idx is not None and default_idx >= 0:
        dev = devices[default_idx]
        if dev['max_input_channels'] > 0:
            print(f"⚠️  BlackHole not found! Falling back to default input: [{default_idx}] {dev['name']}")
            return default_idx, dev['name']

    print("❌ Error: No audio input device found!")
    print("Please install BlackHole 2ch (https://github.com/ExistentialAudio/BlackHole)")
    print("Run with '--list-devices' to see all detected audio devices.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Fallback Transcoder (FFmpeg MP3 Stream)
# ---------------------------------------------------------------------------
def ensure_ffmpeg_running(sample_rate, channels):
    global ffmpeg_proc, ffmpeg_stdin
    if ffmpeg_proc is not None and ffmpeg_proc.poll() is None:
        return True

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return False

    cmd = [
        ffmpeg_bin,
        "-loglevel", "error",
        "-f", "s16le",
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-i", "pipe:0",
        "-f", "mp3",
        "-b:a", "192k",
        "-flush_packets", "1",
        "pipe:1"
    ]

    try:
        ffmpeg_proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=1024 * 16
        )
        ffmpeg_stdin = ffmpeg_proc.stdin
        return True
    except Exception as e:
        print(f"Failed to start FFmpeg transcoder: {e}")
        return False

def stop_ffmpeg():
    global ffmpeg_proc, ffmpeg_stdin
    if ffmpeg_proc:
        try:
            if ffmpeg_stdin:
                ffmpeg_stdin.close()
            ffmpeg_proc.terminate()
            ffmpeg_proc.wait(timeout=1.0)
        except Exception:
            pass
        ffmpeg_proc = None
        ffmpeg_stdin = None

# ---------------------------------------------------------------------------
# HTTP & WebSocket Handlers
# ---------------------------------------------------------------------------
def create_app(auth_token, index_html_path, sample_rate, channels, enable_fallback):
    app = web.Application()

    def check_auth(request):
        if not auth_token:
            return True
        # Check query param ?token=
        query_token = request.query.get("token")
        if query_token == auth_token:
            return True
        # Check Authorization header: Bearer <token>
        auth_hdr = request.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer ") and auth_hdr[7:].strip() == auth_token:
            return True
        return False

    # GET / -> Serve Web Interface
    async def handle_index(request):
        if os.path.exists(index_html_path):
            with open(index_html_path, "r", encoding="utf-8") as f:
                content = f.read()
        else:
            content = "<h1>index.html not found</h1>"
        return web.Response(text=content, content_type="text/html")

    # GET /ws -> WebSocket PCM Stream
    async def handle_ws(request):
        if not check_auth(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.close(code=4001, message=b"Unauthorized")
            return ws

        ws = web.WebSocketResponse(heartbeat=15.0)
        await ws.prepare(request)

        # Max 8 frames buffered (~160ms) to ensure low latency
        queue = asyncio.Queue(maxsize=8)
        client_id = id(queue)
        connected_ws_clients[client_id] = queue

        try:
            # Dedicated background sender task per client
            async def sender_task():
                while True:
                    data = await queue.get()
                    await ws.send_bytes(data)
                    queue.task_done()

            send_coro = asyncio.create_task(sender_task())

            async for msg in ws:
                if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.ERROR):
                    break
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            connected_ws_clients.pop(client_id, None)
            if 'send_coro' in locals():
                send_coro.cancel()
            await ws.close()

        return ws

    # GET /stream or /stream.mp3 -> HTTP Chunked Audio Stream (Fallback)
    async def handle_stream(request):
        if not check_auth(request):
            return web.Response(status=401, text="Unauthorized: Token required")

        ffmpeg_ok = ensure_ffmpeg_running(sample_rate, channels)
        content_type = "audio/mpeg" if ffmpeg_ok else "audio/wav"

        resp = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': content_type,
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Connection': 'keep-alive',
                'Access-Control-Allow-Origin': '*',
                'X-Accel-Buffering': 'no'
            }
        )
        await resp.prepare(request)

        queue = asyncio.Queue(maxsize=16)
        client_id = id(queue)
        connected_http_clients[client_id] = queue

        try:
            while True:
                chunk = await queue.get()
                await resp.write(chunk)
                queue.task_done()
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            connected_http_clients.pop(client_id, None)
            if not connected_http_clients and not enable_fallback:
                stop_ffmpeg()

        return resp

    app.router.add_get("/", handle_index)
    app.router.add_get("/ws", handle_ws)
    app.router.add_get("/stream", handle_stream)
    app.router.add_get("/stream.mp3", handle_stream)
    app.router.add_get("/stream.wav", handle_stream)

    return app

# ---------------------------------------------------------------------------
# Terminal VU Level Meter Loop
# ---------------------------------------------------------------------------
async def terminal_meter_task():
    """Render live ASCII audio level meter on a single line."""
    while True:
        await asyncio.sleep(0.1) # 10 FPS
        rms = meter_state.get('rms', 0.0)
        peak = meter_state.get('peak', 0.0)
        clipped = meter_state.get('clipped', False)

        db = 20.0 * math.log10(max(rms, 1e-4))
        clients = len(connected_ws_clients) + len(connected_http_clients)

        # 24-character VU bar calibrated between -60 dBFS and 0 dBFS
        pct = min(1.0, max(0.0, (db + 60.0) / 60.0))
        filled = int(pct * 24)
        bar = "█" * filled + "░" * (24 - filled)

        clip_badge = " \033[1;91m[CLIP! Turn down Mac volume]\033[0m" if clipped else ""
        print(
            f"\r\033[K\033[1;36m[Audio VU]\033[0m [{bar}] {db:5.1f} dBFS | "
            f"Clients: \033[1;32m{clients}\033[0m{clip_badge}",
            end="",
            flush=True
        )

# ---------------------------------------------------------------------------
# Background FFmpeg Output Reader
# ---------------------------------------------------------------------------
async def ffmpeg_reader_task():
    """Read transcoded MP3 bytes from FFmpeg and distribute to HTTP clients."""
    global ffmpeg_proc
    while True:
        await asyncio.sleep(0.02)
        if ffmpeg_proc and ffmpeg_proc.stdout and connected_http_clients:
            try:
                data = await asyncio.to_thread(ffmpeg_proc.stdout.read, 4096)
                if data:
                    for queue in list(connected_http_clients.values()):
                        if queue.full():
                            try:
                                queue.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        queue.put_nowait(data)
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Stream live macOS system audio (BlackHole) to any local browser with ultra-low latency."
    )
    parser.add_argument("--host", default="0.0.0.0", help="Binding IP (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Web server port (default: 8000)")
    parser.add_argument("--device", default=None, help="Input device ID or name (default: auto-detect BlackHole)")
    parser.add_argument("--list-devices", action="store_true", help="List audio input/output devices and exit")
    parser.add_argument("--samplerate", type=int, default=48000, help="Sample rate in Hz (default: 48000)")
    parser.add_argument("--channels", type=int, default=2, help="Channel count (default: 2 stereo)")
    parser.add_argument("--buffer-ms", type=int, default=20, help="Audio packet duration in ms (default: 20ms)")
    parser.add_argument("--password", "--token", dest="token", default="", help="Optional access password/token")
    parser.add_argument("--fallback", action="store_true", help="Always keep HTTP MP3 transcoder running")
    parser.add_argument("--no-qr", action="store_true", help="Do not print terminal QR code")
    args = parser.parse_args()

    if args.list_devices:
        list_audio_devices()
        return

    # Auto-detect or validate audio device
    dev_idx, dev_name = find_blackhole_device(args.device)

    # Calculate frame blocksize (e.g. 20ms at 48000Hz = 960 frames)
    frames_per_buffer = int((args.samplerate * args.buffer_ms) / 1000)

    # Discover LAN IP
    lan_ip = get_lan_ip()
    access_url = f"http://{lan_ip}:{args.port}"
    if args.token:
        access_url += f"/?token={args.token}"

    script_dir = os.path.dirname(os.path.abspath(__file__))
    index_html_path = os.path.join(script_dir, "index.html")

    # Banner
    print("\n" + "=" * 68)
    print("        macOS LIVE AUDIO STREAMER (Low-Latency LAN Web)")
    print("=" * 68)
    print(f"  • Audio Device:   [{dev_idx}] {dev_name}")
    print(f"  • Format:         {args.samplerate} Hz • {args.channels} Channels (Stereo 16-bit PCM)")
    print(f"  • Frame Size:     {frames_per_buffer} frames ({args.buffer_ms} ms)")
    print(f"  • Listening URL:  \033[1;32m{access_url}\033[0m")
    if args.token:
        print(f"  • Security:       Protected with access token")
    if shutil.which("ffmpeg"):
        print(f"  • Transcoder:     FFmpeg detected (HTTP MP3 fallback ready)")
    else:
        print(f"  • Transcoder:     FFmpeg not found (install 'brew install ffmpeg' for MP3 fallback)")
    print("=" * 68)

    if not args.no_qr:
        print_qr_code(access_url)

    print("\n\033[1mReady! Open the URL or scan QR code on your iPad/phone.\033[0m")
    print("Press \033[1;33mCtrl+C\033[0m to stop.\n")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Audio Capture Callback
    def audio_callback(indata, frames, time_info, status):
        raw_bytes = bytes(indata)

        # Real-time peak & RMS calculation for VU Meter
        try:
            samples = np.frombuffer(raw_bytes, dtype=np.int16)
            if len(samples) > 0:
                rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)) / 32768.0)
                peak = float(np.max(np.abs(samples)) / 32768.0)
                meter_state['rms'] = rms
                meter_state['peak'] = peak
                meter_state['clipped'] = bool(peak >= 0.999)
        except Exception:
            pass

        # Broadcast chunk to active WebSocket and HTTP clients
        if connected_ws_clients or connected_http_clients or (ffmpeg_proc and ffmpeg_stdin):
            def broadcast():
                # Feed WebSocket clients (PCM)
                for q in list(connected_ws_clients.values()):
                    if q.full():
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    q.put_nowait(raw_bytes)

                # Feed FFmpeg stdin if active
                if ffmpeg_stdin:
                    try:
                        ffmpeg_stdin.write(raw_bytes)
                        ffmpeg_stdin.flush()
                    except Exception:
                        pass
                elif connected_http_clients and not ffmpeg_proc:
                    # Feed raw WAV chunk to HTTP clients if FFmpeg is not available
                    for q in list(connected_http_clients.values()):
                        if q.full():
                            try:
                                q.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        q.put_nowait(raw_bytes)

            loop.call_soon_threadsafe(broadcast)

    # Initialize Audio Stream
    try:
        stream = sd.RawInputStream(
            samplerate=args.samplerate,
            blocksize=frames_per_buffer,
            device=dev_idx,
            channels=args.channels,
            dtype='int16',
            callback=audio_callback
        )
    except Exception as e:
        print(f"\n❌ Failed to open audio device [{dev_idx}]: {e}")
        print("Please check Terminal microphone permissions in:")
        print("  System Settings > Privacy & Security > Microphone")
        sys.exit(1)

    app = create_app(args.token, index_html_path, args.samplerate, args.channels, args.fallback)
    runner = web.AppRunner(app)

    async def start_server():
        await runner.setup()
        site = web.TCPSite(runner, args.host, args.port)
        await site.start()
        # Start background tasks
        asyncio.create_task(terminal_meter_task())
        asyncio.create_task(ffmpeg_reader_task())

    try:
        loop.run_until_complete(start_server())
        stream.start()
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        print("\n\nStopping audio stream...")
    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass
        stop_ffmpeg()
        loop.run_until_complete(runner.cleanup())
        loop.close()
        print("Server shutdown complete. Goodbye!\n")

if __name__ == "__main__":
    main()
