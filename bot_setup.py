# bot_setup.py
# ----------------------------------------
# Discord Music Bot (yt_dlp + FFmpeg)
# Cloud-Ready Version (Railway / Render / Replit)
# ----------------------------------------

import os
import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
from collections import deque
import asyncio
import concurrent.futures
from keep_alive import keep_alive  # server web per pingarlo e tenerlo attivo

# ----------------------------------------
# TOKEN (preso dalle variabili d'ambiente)
# ----------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("❌ Environment variable DISCORD_TOKEN not found.")

# ----------------------------------------
# Song queue per ogni server
# ----------------------------------------
SONG_QUEUES = {}


# ----------------------------------------
# Funzione di ricerca asincrona yt_dlp
# ----------------------------------------
async def search_ytdlp_async(query, ydl_opts):
    loop = asyncio.get_running_loop()
    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            # Timeout di 10 secondi
            return await asyncio.wait_for(
                loop.run_in_executor(pool, lambda: _extract(query, ydl_opts)),
                timeout=10.0
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


# ----------------------------------------
# Setup del bot Discord
# ----------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ {bot.user} is online and slash commands are synced!")


# ----------------------------------------
# /play
# ----------------------------------------
@bot.tree.command(name="play", description="Play a song or add it to the queue.")
@app_commands.describe(song_query="Search query or YouTube URL")
async def play(interaction: discord.Interaction, song_query: str):
    await interaction.response.defer()

    if not interaction.user.voice:
        await interaction.followup.send("❌ You must be in a voice channel.")
        return

    voice_channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client

    if vc is None:
        vc = await voice_channel.connect()
    elif vc.channel != voice_channel:
        await vc.move_to(voice_channel)

    ydl_options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "extract_flat": False,
        "geo_bypass": True,
        "source_address": "0.0.0.0",
    }

    query = f"ytsearch1:{song_query}"
    print(f"🔎 Searching for: {song_query}")
    results = await search_ytdlp_async(query, ydl_options)

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


# ----------------------------------------
# /skip
# ----------------------------------------
@bot.tree.command(name="skip", description="Skip the current song.")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await interaction.response.send_message("⏭️ Skipped the current song.")
    else:
        await interaction.response.send_message("❌ Nothing is playing.")


# ----------------------------------------
# /pause
# ----------------------------------------
@bot.tree.command(name="pause", description="Pause the current song.")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc:
        return await interaction.response.send_message("❌ I'm not in a voice channel.")
    if not vc.is_playing():
        return await interaction.response.send_message("❌ Nothing is playing.")
    vc.pause()
    await interaction.response.send_message("⏸️ Paused.")


# ----------------------------------------
# /resume
# ----------------------------------------
@bot.tree.command(name="resume", description="Resume playback.")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc:
        return await interaction.response.send_message("❌ I'm not in a voice channel.")
    if not vc.is_paused():
        return await interaction.response.send_message("❌ I'm not paused.")
    vc.resume()
    await interaction.response.send_message("▶️ Resumed.")


# ----------------------------------------
# /stop
# ----------------------------------------
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


# ----------------------------------------
# Funzione per suonare i brani in coda
# ----------------------------------------
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


# ----------------------------------------
# Avvio del web server + bot
# ----------------------------------------
if __name__ == "__main__":
    keep_alive()  # Avvia il mini server Flask per UptimeRobot
    bot.run(TOKEN)
