# bot_setup.py
# ------------------------------------------------------
# Discord Music Bot - Final Cloud-Ready Version
# Uses: discord.py, yt-dlp, FFmpeg, Flask keep-alive
# Compatible with Render / Replit / Railway
# ------------------------------------------------------

import os
import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
from collections import deque
import asyncio
import concurrent.futures
from keep_alive import keep_alive  # mini webserver Flask to keep the instance alive

# -------------------------
# Environment / tokens
# -------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("❌ Environment variable DISCORD_TOKEN not found.")

# Optional: paste your exported cookies content into the Render secret YOUTUBE_COOKIES
COOKIES_CONTENT = os.getenv("YOUTUBE_COOKIES")
COOKIES_PATH = None

if COOKIES_CONTENT:
    COOKIES_PATH = "/tmp/youtube_cookies.txt"
    try:
        with open(COOKIES_PATH, "w", encoding="utf-8") as f:
            f.write(COOKIES_CONTENT)
        print("🍪 YOUTUBE_COOKIES written to", COOKIES_PATH)
    except Exception as e:
        print("❌ Could not write cookies:", e)
        COOKIES_PATH = None

# -------------------------
# Global queue (per-guild)
# -------------------------
SONG_QUEUES: dict[str, deque] = {}

# -------------------------
# Base options for yt-dlp
# -------------------------
BASE_YTDL_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "geo_bypass": True,
    "nocheckcertificate": True,
    "source_address": "0.0.0.0",
    "extractor_retries": 5,
    "noplaylist": True,
    "default_search": "ytsearch",
    "age_limit": 0,
    "extractor_args": {"youtube": {"player_client": ["android"]}},
}

if COOKIES_PATH:
    BASE_YTDL_OPTS["cookiefile"] = COOKIES_PATH
    # When cookies are provided, android client isn't necessary and may conflict
    BASE_YTDL_OPTS["extractor_args"]["youtube"].pop("player_client", None)
    print("🍪 Using YouTube cookies (web client mode).")
else:
    print("🔎 No cookies found: using public/android client for yt-dlp extraction.")


# -------------------------
# Async wrapper for yt-dlp extraction with timeout
# -------------------------
async def search_ytdlp_async(query: str, ydl_opts: dict):
    loop = asyncio.get_running_loop()
    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return await asyncio.wait_for(
                loop.run_in_executor(pool, lambda: _extract_info(query, ydl_opts)),
                timeout=20.0,
            )
    except asyncio.TimeoutError:
        print(f"❌ [yt_dlp] Timeout searching: {query}")
        return None
    except Exception as e:
        print(f"❌ [yt_dlp] Exception: {e}")
        return None


def _extract_info(query: str, ydl_opts: dict):
    # Use a fresh dict to avoid side-effects
    opts = dict(ydl_opts)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(query, download=False)


# -------------------------
# Discord bot setup
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
    except Exception:
        pass
    print(f"✅ {bot.user} is online and slash commands are synced!")


# -------------------------
# Helper: safe play_next_song
# -------------------------
async def play_next_song(vc: discord.voice_client.VoiceClient, guild_id: str, channel: discord.abc.Messageable):
    queue = SONG_QUEUES.get(guild_id)
    if not queue or len(queue) == 0:
        try:
            await channel.send("✅ Queue finished — disconnecting.")
        except Exception:
            pass
        try:
            if vc and vc.is_connected():
                await vc.disconnect()
        except Exception as e:
            print("❌ Error disconnecting:", e)
        SONG_QUEUES[guild_id] = deque()
        return

    audio_url, title = queue.popleft()
    print(f"🎧 Attempting to play: {title} -> {audio_url}")

    ffmpeg_options = {
        "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        "options": "-vn",
    }

    try:
        source = discord.FFmpegOpusAudio(audio_url, **ffmpeg_options)
        # play and ensure next is scheduled
        vc.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next_song(vc, guild_id, channel), asyncio.get_event_loop()
            ),
        )
        try:
            await channel.send(f"🎶 Now playing: **{title}**")
        except Exception:
            pass
    except Exception as e:
        print("❌ FFmpeg/Playback error:", e)
        try:
            await channel.send(f"❌ Could not play **{title}** — {e}")
        except Exception:
            pass
        # Try next track
        await play_next_song(vc, guild_id, channel)


# -------------------------
# /play command
# -------------------------
@bot.tree.command(name="play", description="Play a song or add it to the queue.")
@app_commands.describe(song_query="Search query or YouTube URL")
async def play(interaction: discord.Interaction, song_query: str):
    # Send an immediate reply to avoid Unknown Interaction 404
    try:
        await interaction.response.send_message("🎵 Searching for your song...", ephemeral=False)
    except Exception as e:
        # If sending initial message fails, try to continue but guard later followups
        print("⚠️ initial response failed:", e)

    # main workflow in try/except so we can report errors gracefully
    try:
        if not interaction.user.voice:
            await interaction.followup.send("❌ You must be in a voice channel.")
            return

        voice_channel = interaction.user.voice.channel
        vc = interaction.guild.voice_client

        # connect or move
        try:
            if vc is None:
                vc = await voice_channel.connect()
            elif vc.channel != voice_channel:
                await vc.move_to(voice_channel)
        except Exception as e:
            print("❌ Voice connect/move error:", e)
            await interaction.followup.send("❌ Could not join your voice channel.")
            return

        # prepare ytdlp options copy
        ytdl_opts = dict(BASE_YTDL_OPTS)

        # safer extractor clients (when no cookies use android; with cookies we removed android earlier)
        if "extractor_args" in ytdl_opts and "youtube" in ytdl_opts["extractor_args"]:
            # prefer tv_embedded + ios as robust fallback
            ytdl_opts["extractor_args"]["youtube"]["player_client"] = ["tv_embedded", "ios"]

        query = f"ytsearch1:{song_query}"
        print("🔎 yt-dlp query:", query)

        results = await search_ytdlp_async(query, ytdl_opts)
        if not results:
            await interaction.followup.send("❌ Could not fetch results. Try again later.")
            return

        # yt-dlp can return single dict or playlist dict
        tracks = results.get("entries") if isinstance(results, dict) and results.get("entries") else None
        if tracks is None:
            # maybe results is already a single track
            tracks = [results]

        if not tracks:
            await interaction.followup.send("❌ No results found.")
            return

        first = tracks[0]
        # try to obtain a usable audio url; yt-dlp sometimes provides 'url' or must use 'webpage_url'
        audio_url = first.get("url") or first.get("webpage_url")
        title = first.get("title") or first.get("id") or "Untitled"

        if not audio_url:
            await interaction.followup.send("❌ Could not obtain audio URL for the result.")
            return

        print(f"🎵 Found: {title} -> {audio_url}")

        guild_id = str(interaction.guild_id)
        SONG_QUEUES.setdefault(guild_id, deque()).append((audio_url, title))

        # if currently playing, inform user we've queued, else start playback
        if vc.is_playing() or vc.is_paused():
            await interaction.followup.send(f"➕ Added to queue: **{title}**")
        else:
            await interaction.followup.send(f"🎶 Now playing: **{title}**")
            # start playback (fire and forget)
            await play_next_song(vc, guild_id, interaction.channel)

    except Exception as exc:
        print("❌ Exception in /play:", exc)
        try:
            await interaction.followup.send(f"❌ Error while processing your request: {exc}")
        except Exception:
            pass


# -------------------------
# /skip
# -------------------------
@bot.tree.command(name="skip", description="Skip the current song.")
async def skip(interaction: discord.Interaction):
    try:
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("⏭️ Skipped the current song.")
        else:
            await interaction.response.send_message("❌ Nothing is playing.")
    except Exception as e:
        print("❌ /skip error:", e)
        try:
            await interaction.response.send_message("❌ Error skipping track.")
        except Exception:
            pass


# -------------------------
# /pause
# -------------------------
@bot.tree.command(name="pause", description="Pause the current song.")
async def pause(interaction: discord.Interaction):
    try:
        vc = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("❌ I'm not in a voice channel.")
        if not vc.is_playing():
            return await interaction.response.send_message("❌ Nothing is playing.")
        vc.pause()
        await interaction.response.send_message("⏸️ Paused.")
    except Exception as e:
        print("❌ /pause error:", e)
        try:
            await interaction.response.send_message("❌ Error pausing.")
        except Exception:
            pass


# -------------------------
# /resume
# -------------------------
@bot.tree.command(name="resume", description="Resume playback.")
async def resume(interaction: discord.Interaction):
    try:
        vc = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("❌ I'm not in a voice channel.")
        if not vc.is_paused():
            return await interaction.response.send_message("❌ I'm not paused.")
        vc.resume()
        await interaction.response.send_message("▶️ Resumed.")
    except Exception as e:
        print("❌ /resume error:", e)
        try:
            await interaction.response.send_message("❌ Error resuming.")
        except Exception:
            pass


# -------------------------
# /stop
# -------------------------
@bot.tree.command(name="stop", description="Stop playback and clear the queue.")
async def stop(interaction: discord.Interaction):
    try:
        vc = interaction.guild.voice_client
        if not vc or not vc.is_connected():
            return await interaction.response.send_message("❌ I'm not connected to any voice channel.")

        guild_id_str = str(interaction.guild_id)
        SONG_QUEUES.pop(guild_id_str, None)

        if vc.is_playing() or vc.is_paused():
            vc.stop()

        await vc.disconnect()
        await interaction.response.send_message("⏹️ Stopped playback and disconnected.")
    except Exception as e:
        print("❌ /stop error:", e)
        try:
            await interaction.response.send_message("❌ Error stopping playback.")
        except Exception:
            pass


# -------------------------
# Start keep-alive server and run bot
# -------------------------
if __name__ == "__main__":
    # start flask server (keep_alive.keep_alive starts a background thread)
    try:
        keep_alive()
    except Exception as e:
        print("⚠️ keep_alive failed:", e)

    # run the bot
    bot.run(TOKEN)
