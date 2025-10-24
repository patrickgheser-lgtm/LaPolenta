# bot_setup.py
# ------------------------------------------------------
# Discord Music Bot - Cloud Ready Version (Render / Replit / Railway)
# Uses yt_dlp + FFmpeg + Flask keep-alive server
# ------------------------------------------------------

import os
import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
from collections import deque
import asyncio
import concurrent.futures
from keep_alive import keep_alive  # mini webserver Flask

# ------------------------------------------------------
# Load Discord token
# ------------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("❌ Environment variable DISCORD_TOKEN not found.")

# ------------------------------------------------------
# Global queues
# ------------------------------------------------------
SONG_QUEUES = {}

# ------------------------------------------------------
# yt-dlp configuration (cookies optional)
# ------------------------------------------------------
cookies_content = os.getenv("YOUTUBE_COOKIES")
cookies_path = None

if cookies_content:
    cookies_path = "/tmp/youtube_cookies.txt"
    with open(cookies_path, "w", encoding="utf-8") as f:
        f.write(cookies_content)

BASE_YTDL_OPTS = {
    'format': 'bestaudio/best',
    'quiet': True,
    'geo_bypass': True,
    'nocheckcertificate': True,
    'source_address': '0.0.0.0',
    'extractor_retries': 5,
    'noplaylist': True,
    'default_search': 'ytsearch',
    'age_limit': 0,
    'extractor_args': {'youtube': {'player_client': ['android']}},
}

if cookies_path:
    BASE_YTDL_OPTS['cookiefile'] = cookies_path
    print("🍪 YouTube cookies loaded successfully.")


# ------------------------------------------------------
# yt-dlp async search
# ------------------------------------------------------
async def search_ytdlp_async(query, ydl_opts):
    loop = asyncio.get_running_loop()
    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return await asyncio.wait_for(
                loop.run_in_executor(pool, lambda: _extract(query, ydl_opts)),
                timeout=15.0
            )
    except asyncio.TimeoutError:
        print(f"❌ [yt_dlp] Timeout while searching: {query}")
        return None
    except Exception as e:
        print(f"❌ [yt_dlp] Error: {e}")
        return None


def _extract(query, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)


# ------------------------------------------------------
# Discord bot setup
# ------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ {bot.user} is online and slash commands are synced!")


# ------------------------------------------------------
# /play command
# ------------------------------------------------------
@bot.tree.command(name="play", description="Play a song or add it to the queue.")
@app_commands.describe(song_query="Search query or YouTube URL")
async def play(interaction: discord.Interaction, song_query: str):
    # Defer early to avoid Discord 404
    await interaction.response.defer(thinking=True)

    if not interaction.user.voice:
        await interaction.followup.send("❌ You must be in a voice channel.")
        return

    voice_channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client

    if vc is None:
        vc = await voice_channel.connect()
    elif vc.channel != voice_channel:
        await vc.move_to(voice_channel)

    ytdl_opts = BASE_YTDL_OPTS.copy()

    query = f"ytsearch1:{song_query}"
    print(f"🔎 Searching for: {song_query}")
    results = await search_ytdlp_async(query, ytdl_opts)

    if not results:
        await interaction.followup.send("❌ Could not fetch results. Try again later.")
        return

    tracks = results.get("entries", [])
    if not tracks:
        await interaction.followup.send("❌ No results found.")
        return

    first_track = tracks[0]
    audio_url = first_track.get("url")
    title = first_track.get("title", "Untitled")

    print(f"🎵 Found: {title}")

    guild_id = str(interaction.guild_id)
    SONG_QUEUES.setdefault(guild_id, deque()).append((audio_url, title))

    if vc.is_playing() or vc.is_paused():
        await interaction.followup.send(f"➕ Added to queue: **{title}**")
    else:
        await interaction.followup.send(f"🎶 Now playing: **{title}**")
        await play_next_song(vc, guild_id, interaction.channel)


# ------------------------------------------------------
# /skip command
# ------------------------------------------------------
@bot.tree.command(name="skip", description="Skip the current song.")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await interaction.response.send_message("⏭️ Skipped the current song.")
    else:
        await interaction.response.send_message("❌ Nothing is playing.")


# ------------------------------------------------------
# /pause command
# ------------------------------------------------------
@bot.tree.command(name="pause", description="Pause the current song.")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc:
        return await interaction.response.send_message("❌ I'm not in a voice channel.")
    if not vc.is_playing():
        return await interaction.response.send_message("❌ Nothing is playing.")
    vc.pause()
    await interaction.response.send_message("⏸️ Paused.")


# ------------------------------------------------------
# /resume command
# ------------------------------------------------------
@bot.tree.command(name="resume", description="Resume playback.")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc:
        return await interaction.response.send_message("❌ I'm not in a voice channel.")
    if not vc.is_paused():
        return await interaction.response.send_message("❌ I'm not paused.")
    vc.resume()
    await interaction.response.send_message("▶️ Resumed.")


# ------------------------------------------------------
# /stop command
# ------------------------------------------------------
@bot.tree.command(name="stop", description="Stop playback and clear queue.")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_connected():
        return await interaction.response.send_message("❌ I'm not connected to any voice channel.")

    guild_id_str = str(interaction.guild_id)
    SONG_QUEUES.pop(guild_id_str, None)

    if vc.is_playing() or vc.is_paused():
        vc.stop()

    await vc.disconnect()
    await interaction.response.send_message("⏹️ Stopped playback and disconnected.")


# ------------------------------------------------------
# Queue playback handler
# ------------------------------------------------------
async def play_next_song(vc, guild_id, channel):
    if SONG_QUEUES[guild_id]:
        audio_url, title = SONG_QUEUES[guild_id].popleft()
        print(f"🎧 Playing: {title}")

        ffmpeg_options = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn",
        }

        try:
            source = discord.FFmpegOpusAudio(audio_url, **ffmpeg_options)
            vc.play(
                source,
                after=lambda e: asyncio.run_coroutine_threadsafe(
                    play_next_song(vc, guild_id, channel),
                    asyncio.get_event_loop(),
                ),
            )
            await channel.send(f"🎶 Now playing: **{title}**")
        except Exception as e:
            print(f"❌ FFmpeg error: {e}")
            await channel.send(f"❌ Could not play **{title}**.")
    else:
        await channel.send("✅ Queue finished — disconnecting.")
        await vc.disconnect()
        SONG_QUEUES[guild_id] = deque()


# ------------------------------------------------------
# Start Flask webserver + Discord bot
# ------------------------------------------------------
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)
